import json, uuid, re, time, traceback, tornado, tornado.websocket
from pprint import pprint
from construct_auth import construct_socket_auth
from construct_cmd_parser import ConstructCmdParser

CONSTRUCT_PROTOCOL_VERSION = [0,3,0]

class ConstructSocketHandler(tornado.websocket.WebSocketHandler):
  clients = []

  def on_sensor_changed(self):
    for name in ['bed', 'extruder']:
      self.send(
        sensor_changed= {'name': name, 'value': prontserve.sensors[name]},
      )

  def on_uncaught_event(self, event_name, data):
    listener = "on_%s"%event_name

    if event_name[:4] == 'job_' and event_name != "job_progress_changed":
      data = prontserve.jobs.sanitize(data)
    self.send({event_name: data})

  def _execute(self, transforms, *args, **kwargs):
    self.authorized = construct_socket_auth(self)
    super(ConstructSocketHandler, self)._execute(transforms, *args, **kwargs)

  def select_subprotocol(self, subprotocols):
    # Chooses a compatible construct protocol from the list sent by the 
    # client.
    # The Construct Protocol uses semantic version v2.0.0 so any minor version 
    # w/ a specific major version will be compatible with all minor versions 
    # before it within that same major version.
    # The only exception to this rule is major version 0.
    # Version 0 protocols are unstable and can break compatibility more often.
    # We are only going to cause backwards incompatibility in 0.x minor version
    # See http://semver.org/
    server_v = CONSTRUCT_PROTOCOL_VERSION
    compatible_v = [-1, -1]
    for p in list(subprotocols):
      regex = '^construct\.text\.([0-9]+)\.?([0-9]+)'
      client_v = [int(s) for s in list(re.search(regex, p).groups())]
      pprint(client_v)
      if client_v[0] == server_v[0] and client_v[1] <= server_v[1]:
        if client_v[1] > compatible_v[1]: compatible_v = client_v

    print subprotocols
    print compatible_v

    # On incompatibility: Return a BS version and sending an error once the 
    # connection opens.
    self.client_versions = subprotocols
    self.compatible = compatible_v[1] > -1
    pprint(self.compatible)
    print self._protocol_str(compatible_v)
    if not self.compatible: return subprotocols[0]
    return self._protocol_str(compatible_v)

  def _protocol_str(self, v):
    return "construct.text.%i.%i"%(v[0], v[1])

  def open(self):
    if not self.authorized:
      self._error(
        message= "Incorrect password or username",
        type= 'auth.sync'
      )
    if not self.compatible:
      v = self._protocol_str(CONSTRUCT_PROTOCOL_VERSION)
      msg = """
      Incompatible Construct Protocol version.

      Server version: %s
      Your version(s): %s
      """%(v, str(self.client_versions))
      self._error(message= msg, type= 'version.sync')

    if not (self.authorized and self.compatible): return self.stream.close()

    self.session_uuid = str(uuid.uuid4())
    self.application.add_client(self)
    self.send( self.application.build_initialized_event(self) )

    open_clients = len(self.application.clients)
    print "WebSocket opened. %i sockets currently open." % open_clients

  def on_message(self, msg):
    if not self.authorized: return
    # Parsing the command
    try:
      cmd = ConstructCmdParser(msg)
    except Exception as ex:
      print traceback.format_exc()
      return self._error(message= str(ex), type= 'syntax.sync')
    # Running the command
    try:
      self.send([{"type": "ack", "data": self.application.run_cmd(cmd)}])
    except Exception as ex:
      print traceback.format_exc()
      self._error(message= str(ex), type= 'runtime.sync')

  def _error(self, **kwargs):
    self.send([{"type": "error", "data": kwargs}])

  def send(self, message):
    print "sending:"
    print message
    self.write_message(json.dumps(message))

  def on_close(self):
    if self.session_uuid in self.application.clients:
      self.application.remove_client(self)
    open_clients = len(self.application.clients)
    print "WebSocket closed. %i sockets currently open." % open_clients
