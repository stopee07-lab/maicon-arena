"""stdlib-only contestant transport. It intentionally has no organizer API methods."""
import json
import math
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


class ApiError(RuntimeError):
    def __init__(self, status, detail):
        self.status = status
        self.detail = detail
        super().__init__(f'HTTP {status}: {detail}')


class TeamClient:
    REPORT_GET = {'me', 'order', 'target', 'drone'}
    REPORT_POST = {'ready', 'interpretation', 'detection', 'situation'}
    SIM_GET = {'state', 'camera'}
    SIM_POST = {'cmd_vel', 'gimbal', 'fire', 'oled', 'strategy'}

    def __init__(self, url, mission_code, timeout=.25, api_prefix='/api/report', robot_prefix='/sim/robot'):
        if urlsplit(url).scheme not in ('http', 'https'):
            raise ValueError('url must use http or https')
        if api_prefix not in ('/api/report', '/api/rehearsal'):
            raise ValueError('unsupported official API prefix')
        if robot_prefix not in ('/sim/robot', '/sim/practice/robot', '/sim/rehearsal/robot'):
            raise ValueError('unsupported own-robot adapter prefix')
        self.robot_prefix=robot_prefix
        if not mission_code:
            raise ValueError('mission code is required')
        self.url, self.mission_code = url.rstrip('/'), mission_code
        self.timeout, self.api_prefix = timeout, api_prefix

    def _request(self, path, payload=None, query=None, raw=False, timeout=None):
        query = dict(query or {})
        headers = {'Accept': 'image/jpeg' if raw else 'application/json'}
        if payload is None:
            query['mission_code'] = self.mission_code
            body = None
        else:
            body = json.dumps({**payload, 'mission_code': self.mission_code}).encode()
            headers['Content-Type'] = 'application/json'
        if query:
            path += '?' + urlencode(query)
        request = Request(self.url + path, data=body, headers=headers)
        try:
            with urlopen(request, timeout=timeout or self.timeout) as response:
                data = response.read()
                return (data, dict(response.headers)) if raw else json.loads(data)
        except HTTPError as exc:
            data = exc.read().decode('utf-8', errors='replace')
            exc.close()
            try: data = json.loads(data)
            except ValueError: pass
            raise ApiError(exc.code, data) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ApiError(0, str(exc)) from exc

    def report_get(self, name, **query):
        if name not in self.REPORT_GET:
            raise ValueError('not a contestant report endpoint')
        if name == 'drone' and self.api_prefix != '/api/report':
            raise ValueError('rehearsal has no drone endpoint')
        wait = min(120, max(0, int(query.get('wait', 0))))
        if 'wait' in query: query['wait'] = wait
        return self._request(self.api_prefix+'/'+name, query=query, raw=name=='drone', timeout=max(self.timeout, wait+2) if wait else None)

    def report_post(self, name, payload):
        if name not in self.REPORT_POST:
            raise ValueError('not a contestant report endpoint')
        return self._request(self.api_prefix+'/'+name, payload=payload)

    def sim_get(self, name):
        if name not in self.SIM_GET: raise ValueError('not an own-robot sensor endpoint')
        return self._request(self.robot_prefix+'/'+name, raw=name=='camera')

    def sim_post(self, name, payload=None):
        if name not in self.SIM_POST: raise ValueError('not an own-robot actuator endpoint')
        return self._request(self.robot_prefix+'/'+name, payload=payload or {})

    def stop(self):
        return self.sim_post('cmd_vel', {'linear_x':0., 'angular_z':0.})


class SafeMotion:
    """Two freshness gates: sensor <= .5s, command <= .5s. Locked transitions discard motion."""
    def __init__(self, clock=time.monotonic, watchdog=.5):
        self.clock, self.watchdog = clock, watchdog
        self.state, self.state_at, self.command_at = None, -math.inf, -math.inf
        self.velocity = (0., 0.)

    @staticmethod
    def allowed(state):
        return bool(state) and (state.get('mode') == 'practice' or state.get('cmd') in ('start','resume'))

    def observe(self, state):
        was_allowed = self.allowed(self.state)
        self.state, self.state_at = dict(state), self.clock()
        if not self.allowed(state) or not was_allowed:
            self.clear()

    def clear(self):
        self.velocity, self.command_at = (0.,0.), -math.inf

    def command(self, linear, angular):
        if not all(math.isfinite(float(v)) for v in (linear, angular)):
            raise ValueError('velocity must be finite')
        if not self.allowed(self.state):
            self.clear(); return
        self.velocity = (max(-.6,min(.6,float(linear))), max(-3.,min(3.,float(angular))))
        self.command_at = self.clock()

    def output(self):
        now = self.clock()
        if (not self.allowed(self.state) or now-self.state_at >= self.watchdog
                or now-self.command_at >= self.watchdog):
            self.clear()
            return (0.,0.)
        return self.velocity


class ReportWorkflow:
    """M6 retry worker; call only with a fresh /me phase. No scores or enemy truth."""
    def __init__(self, client, clock=time.monotonic, budget=25.):
        self.client, self.clock, self.budget = client, clock, budget
        self.payload = None; self.deadline = 0.; self.next_try = 0.
        self.done = False; self.failed = False; self.attempts = 0

    def begin(self, ranking, deadline=None):
        if self.payload is not None: return
        ranking = [str(x).upper() for x in ranking]
        if not 1 <= len(ranking) <= 3 or len(set(ranking)) != len(ranking):
            raise ValueError('ranking must contain 1 to 3 unique opponent callsigns')
        self.payload = {'ranking': list(ranking)}
        self.deadline = min(self.clock()+self.budget,deadline) if deadline is not None else self.clock()+self.budget

    def tick(self, phase):
        if phase == 'finished': self.failed = not self.done
        if self.done: return True
        if not self.payload or self.failed or phase != 'reporting': return False
        if self.clock() >= self.deadline:
            self.failed = True; return False
        if self.clock() < self.next_try: return False
        self.attempts += 1
        try:
            response = self.client.report_post('situation', self.payload)
        except ApiError as exc:
            if exc.status not in (0, 409, 429, 500, 502, 503, 504):
                self.failed = True; raise
            self.next_try = self.clock() + min(1., .2 * 2 ** min(self.attempts-1, 3))
            return False
        if response.get('accepted') is True:
            echo = response.get('received',{}).get('ranking')
            if echo != self.payload['ranking']:
                self.failed = True
                raise ValueError('server did not echo the requested complete ranking')
            self.done = True
            return True
        self.next_try = self.clock()+.5
        return False


def forward_clearance(state, half_angle=math.radians(25)):
    """Minimum valid range in the front arc; a nearby rear wall is not an obstacle ahead."""
    scan=state.get('scan',{})
    start=scan.get('angle_min',0.);increment=scan.get('angle_increment',0.)
    values=[]
    for i,distance in enumerate(scan.get('ranges',[])):
        angle=start+i*increment
        angle=math.atan2(math.sin(angle),math.cos(angle))
        if abs(angle)<=half_angle and isinstance(distance,(int,float)) and math.isfinite(distance) and distance>=0:
            values.append(float(distance))
    return min(values) if values else None


def sensors_healthy(state):
    return state.get('camera_available') is True and forward_clearance(state) is not None
