import signal, time, sys, glob, os, codecs, pybonjour, atexit, tornado
import inflection, copy
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
      motors= dict(motors_enabled= False)
    )
    for t, default_vals in self.component_defaults.iteritems():
      for key in kwargs["components"][inflection.pluralize(t)]:
        self.components[key] = copy.deepcopy(default_vals)
        self.components[key]["type"] = t

    # Setting the printer's initial values
    self.c_settings = kwargs["settings"]
    self.sensor_update_received = True
    self.waiting_to_reach_temp = None
    self.status = "idle"
    self.reset_timeout = 0

  component_defaults = dict(
    temp = dict(
      target_temp = 0,
      target_temp_progress = dict(eta= 0, percent= 100),
      current_temp = -1,
    ),
    fan = dict( fan_speed = 255, fan_enabled = False ),
    conveyor = dict( conveyor_speed = 255, conveyor_enabled = False ),
    axis = dict( position = 0 )
  )

  def start(self):
    # Start the print queue and sensor polling
    tornado.ioloop.PeriodicCallback(
      self.jobs.iterate_print_job_loop, 300, self.ioloop
    ).start()
    tornado.ioloop.PeriodicCallback(
      self.iterate_sensor_loop, self.c_settings.sensor_poll_rate*1000, self.ioloop
    ).start()
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

  def iterate_sensor_loop(self):
    # A number of conditions that must be met for us to send a temperature 
    # request to the printer. This safeguards this printer from being overloaded
    # by temperature requests it cannot presently respond to.
    if not self.sensor_update_received: return
    if not (time.time() - self.reset_timeout) > 0: return
    if not (not self.waiting_to_reach_temp): return

    self.sensor_update_received = False
    self.printer.request_sensor_update()

  def on_uncaught_event(self, event_name, data=None):
    self.broadcast([dict(type= event_name, data= data)])

  def set_status(self, value):
    self.set_printer_attr("status", value)
    if value != "printing": self.jobs.current_job = None

  def set_waiting_to_reach_temp(self, value):
    self.set_printer_attr("waiting_to_reach_temp", value)

  def set_printer_attr(self, key, value):
    setattr(self, key, value)
    self.broadcast([dict(type="%s_changed"%key, data=value)])

  def set_sensor_update_received(self, value):
    self.sensor_update_received = value

  # Not thread safe
  def set_reset_timeout(self, timeout):
    self.reset_timeout = timeout
    self.ioloop.add_timeout(timeout, self.reset_complete)

  def reset_complete(self):
    self.set_status("idle")

  def c_set(self, target, key, data, internal=False):
    # raise Exception("Fan speed must be a number")
    # raise Exception("""
    # Bad set fan parameters. Please either use `set fan speed: [NUMBER]` 
    # or `set fan [ON|OFF]`.
    # """)

    if (not target in self.components):
      raise Exception("Target does not exist: %s"%target)
    if (not key in self.components[target]):
      raise Exception("Key does not exist: %s"%key)

    self.components[target][key] = data
    event_type = "%s_changed"%key
    if internal == False:
      self.fire(event_type, dict(target= target, data= data))
    self.broadcast([dict(type= event_type, data= data, target= target)])

  def c_get(self, target, key):
    return self.components[target][key]

  def build_initialized_event(self, client):
    event = dict(type= "initialized", data= dict(
      session_uuid= client.session_uuid,
      status= self.status,
      jobs= self.jobs.public_list(),
      job_progress= self.jobs.previous_job_progress,
      waiting_to_reach_temp= self.waiting_to_reach_temp,
      client_count= len(self.clients)
    ))
    for key, data in self.components.iteritems():
      event["data"][key] = data
    return [event]

  def broadcast(self, events):
    for id, client in self.clients.iteritems(): client.send(events)

  def add_client(self, client):
    self.broadcast([dict(type='client_count_changed', data= len(self.clients))])
    self.clients[client.session_uuid] = client

  def remove_client(self, client):
    del self.clients[client.session_uuid]
    self.broadcast([dict(type='client_count_changed', data= len(self.clients))])

  # Commands
  # --------------------------------------------------------------------------

  def run_cmd(self, c):
    if self.status != "idle" and (c.cmd != "estop" and c.cmd.find("job") == -1):
      raise Exception("Cannot run commands when %s"%self.status)
    for d in [self.printer, self.jobs, self]:
      if hasattr(d, c.method_name): delegate = d
    return getattr(delegate, c.method_name)(*(c.args), **(c.kwargs))

  def do_set(self, *args, **kwargs):
    if(len(args) == 1 and args[0] == "temp"):
      key = "target_temp"
      for target, data in kwargs.iteritems(): self.c_set(target, key, data)
    else:
      for k, v in kwargs.iteritems(): self.set_speed_or_enabled(k, v)

  def set_speed_or_enabled(self, k, v):
    if (type(v) == bool):         key = "%s_enabled"%k
    if (type(v) in [float, int]): key = "%s_speed"%k
    if k == "motors": target = "motors"
    if k == "fan": target = "f0"
    if k == "conveyor": target = "c0"
    self.c_set(target, key, v)

  def do_print(self):
    print self.printer.is_online()
    if not self.printer.is_online(): raise Exception("Not online")
    if self.status == "printing": raise Exception("Already printing")
    no_jobs_msg = "Nothing to print. Try adding a print job with add_job."
    if len(self.jobs.list) == 0: raise Exception(no_jobs_msg)
    self.set_status("printing")

  def do_estop(self):
    self.printer.do_estop()
    self.set_status("estopped")
    # Resetting all the printer's attributes
    for target, attrs in self.components.iteritems():
      if not ("type" in attrs and attrs["type"] in self.component_defaults):
        continue
      for key, data in self.component_defaults[attrs["type"]].iteritems():
        self.c_set(target, key, data, internal = True)
