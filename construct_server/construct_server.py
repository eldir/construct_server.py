import signal, time, sys, glob, os, codecs, pybonjour, atexit, tornado
import inflection, copy, types

# Infastructure
from event_emitter import EventEmitter
from print_job_queue import PrintJobQueue
# Routes
from construct_socket_handler import ConstructSocketHandler
from construct_job_upload_handler import ConstructJobUploadHandler

class ConstructServer(tornado.web.Application, EventEmitter):
  def __init__(self, **kwargs):
    self.printer = kwargs["printer"]
    EventEmitter.__init__(self)

    # Configuring the Web Server
    if not kwargs["routes"]: kwargs["routes"] = []
    routes = kwargs["routes"] + [
      (r"/socket", ConstructSocketHandler),
      (r"/jobs", ConstructJobUploadHandler),
    ]
    server_settings = kwargs["server_settings"]
    if server_settings == None: server_settings = {}
    self.clients = {}
    self.ioloop = tornado.ioloop.IOLoop.instance()
    signal.signal(signal.SIGINT, self.sigint_handler)
    tornado.web.Application.__init__(self, routes, **server_settings)

    # Configuring the print job queue
    self.jobs = PrintJobQueue(self)
    self.jobs.listeners.add(self)

    # Configuring the printer components
    self.components = dict(
      motors = dict(enabled = False),
      jobs = dict(),
      pause_between_prints = True,
      sensor_poll_rate = 3000,
      sessions_count = 0,
      status = 'idle'
    )
    self.components = dict(self.components, **kwargs["settings"])
    for t, default_vals in self.component_defaults.iteritems():
      for key in kwargs["components"][inflection.pluralize(t)]:
        self.components[key] = copy.deepcopy(default_vals)
        self.components[key]["type"] = t

    # Setting the printer's initial values
    self.sensor_update_received = True
    self.reset_timeout = 0
    self.blockers = []

  component_defaults = dict(
    temp = dict(
      current_temp = -1,
      target_temp = 0,
      target_temp_countdown = None,
      blocking = False
    ),
    fan = dict( speed = 255, enabled = False ),
    conveyor = dict( speed = 255, enabled = False ),
    axis = dict( position = 0 )
  )

  def start(self):
    _do = lambda *args: tornado.ioloop.PeriodicCallback(*args).start()
    # Start the print queue and sensor polling
    _do(self.jobs.iterate_print_job_loop, 300, self.ioloop)
    _do(self.poll_temp, self.components['sensor_poll_rate'], self.ioloop)
    # Initialize DNS-SD once the server is ready to go online
    self.init_dns_sd()
    # Start the server
    self.listen(8888)

  def init_dns_sd(self):
    sdRef = pybonjour.DNSServiceRegister(name = None,
                                         regtype = '_construct._tcp',
                                         port = 8888,
                                         domain = "local.")
    atexit.register(self.cleanup_service, sdRef)

  def cleanup_service(self, sdRef):
    sdRef.close()

  def sigint_handler(self, signum, frame):
    print "exiting..."
    self.ioloop.stop()
    raise Exception("Ctrl+C")

  def poll_temp(self):
    # A number of conditions that must be met for us to send a temperature 
    # request to the printer. This safeguards this printer from being overloaded
    # by temperature requests it cannot presently respond to.
    c = (not self.sensor_update_received) or (time.time() < self.reset_timeout)
    if c or len(self.blockers) > 0: return
    # Requesting a temperature update from the printer
    self.sensor_update_received = False
    self.printer.request_sensor_update()

  def set_blocking_temps(self, keys):
    unblocked = [k for k in self.blockers if k not in keys]
    for k in unblocked: self.update_c([k], False)
    for k in keys: self.update_c([k], True)
    self.blockers = keys

  def set_sensor_update_received(self, value):
    self.sensor_update_received = value

  # Not thread safe
  def set_reset_timeout(self, timeout):
    self.reset_timeout = timeout
    self.ioloop.add_timeout(timeout, lambda: self.c_set(['status'], 'idle'))

  def c_add(self, target_path, data, internal= False):
    if 'type' in data: self.fire("add_%s"%data['type'], data, target_path)
    self.c_set(target_path, data, internal=internal, event="add")

  def c_set(self, target_path, data, internal= False, event="change"):
    parent = self.find_parent(target_path, requireKey=(event!="add"))
    key = target_path[-1]
    virtual = (key in parent) and type(parent[key]) == types.FunctionType
    # If the value has not changed there is nothing to do.
    if (key in parent) and parent[key] == data: return
    # do not override virtual attributes. Just skip to firing the event.
    if not virtual: parent[key] = data
    if internal == False:
      # targets without a parent type are fired internally as "my_key_change"
      event_name = "%s_%s"%(key, event)
      # targets with a parent type are fired internally as "type_my_key_change"
      if 'type' in parent:
        event_name = "%s_%s"(parent['type'], event_name)
      self.fire(event_name, target_path[:-1], parent[key], data)
    # Sending the event to all the websocket sessions
    self.broadcast([dict(type= event, data= data, target= target_path)])

  def c_get(self, target_path):
    target_parent = self.find_parent(target_path, requireKey=True)
    return target_parent[target_path.pop()]

  def c_rm(self, target_path):
    target_parent = self.find_parent(target_path, requireKey=True)
    key = target_path.pop()
    data = target_parent[key]
    if 'type' in data: self.fire("rm_%s"%data['type'], data, target_path)
    del target_parent[key]
    self.broadcast([dict(type= "remove", target= target_path)])

  def find_parent(self, path, requireKey=True):
    parent = self.components
    for i, key in enumerate(path):
      if (not key in parent) and (requireKey != False):
        raise Exception("Target does not exist: [%s]"%','.join(path))
      if not i == len(path) - 1: parent = parent[key]
    return parent


  def build_initialized_event(self, client):
    # Adding each component to the data (except jobs, it needs to be modified)
    data = {k:v for k,v in self.components.iteritems() if not k in ['jobs']}
    # Adding the jobs (minus their full text) and this sessions's uuid
    data = dict(data, **dict(
      session_uuid= client.session_uuid,
      jobs= self.jobs.public_list()
    ))
    return [dict(type= "initialized", data= data)]

  def broadcast(self, events):
    for id, client in self.clients.iteritems(): client.send(events)

  def add_client(self, client):
    self.c_set(['sessions_count'], len(self.clients))
    self.clients[client.session_uuid] = client

  def remove_client(self, client):
    del self.clients[client.session_uuid]
    self.c_set(['sessions_count'], len(self.clients))

  # Commands
  # --------------------------------------------------------------------------

  def run_cmd(self, c):
    status = self.c_get(['status'])
    if status != "idle" and (c.cmd != "estop" and c.cmd.find("job") == -1):
      raise Exception("Cannot run commands when %s"%status)
    for d in [self.printer, self.jobs, self]:
      if hasattr(d, c.method_name): delegate = d
    return getattr(delegate, c.method_name)(*(c.args), **(c.kwargs))

  def do_set(self, *args, **kwargs):
    if(len(args) == 1 and args[0] == "temp"):
      key = "target_temp"
      for target, data in kwargs.iteritems(): self.c_set([target, key], data)
    else:
      for k, v in kwargs.iteritems(): self.set_speed_or_enabled(k, v)

  def set_speed_or_enabled(self, k, v):
    if (type(v) == bool):         key = "enabled"
    if (type(v) in [float, int]): key = "speed"
    if k == "motors": target = "motors"
    if k == "fan": target = "f0"
    if k == "conveyor": target = "c0"
    self.c_set([target, key], v)

  def do_change_job(self, *kwargs):
    job_id = kwargs['id']
    del kwargs['id']
    for k, v in kwargs.iteritems(): self.c_set(['jobs', job_id, k], v)

  def do_print(self):
    if not self.printer.is_online(): raise Exception("Not online")
    no_jobs_msg = "Nothing to print. Try adding a print job with add_job."
    if len(self.jobs.list) == 0: raise Exception(no_jobs_msg)
    self.c_set(['status'], 'printing')

  def do_estop(self):
    self.printer.do_estop()
    self.c_set(['status'], 'estopped')
    # Resetting all the printer's attributes
    for target, attrs in self.components.iteritems():
      if type(attrs) != dict: continue
      if not ("type" in attrs and attrs["type"] in self.component_defaults):
        continue
      for key, data in self.component_defaults[attrs["type"]].iteritems():
        self.c_set([target, key], data, internal = True)
