import textwrap, re

class ConstructCmdParser:
  def __init__(self, msg):
    cmds = self._cmds

    print "message received: %s"%(msg)
    msg = re.sub(r'\s+', "\s" ,msg).strip().lower()
    msg = msg.replace("@", "at:").replace(":\s", ":")
    words = msg.split("\s")

    self.cmd = words[0]
    self.method_name = "do_%s"%self.cmd
    self.args = []
    self.kwargs = {}

    for w in words[1:]:
      if w.find(":") > -1:
        k, v = w.split(":")
        if (v in ["on", "off"]): v = (v == "on")
        elif (re.match("[0-9]+$", v)): v = int(v)
        elif (re.match("[0-9\.]+$", v)): v = float(v)
        self.kwargs[k] = v
      else:
        self.args.append(w)

    if not self.cmd in cmds.keys(): self.err('cmd_not_found', self.cmd)
    if not self.is_valid(): self.err('args_err')
    if self.cmd=="set": self.validate_set_cmd()

  def is_valid(self):
    cmd = self.cmd
    t = self._cmds[cmd]['type']

    if (len(self.kwargs) > 0  and t in ['none', 'array', 'home'] ): return False
    if (len(self.args)   > 0  and t in ['none', 'dict']          ): return False
    if (len(self.kwargs) == 0 and t == 'dict' or cmd == 'set'    ): return False
    if (len(self.args)   == 0 and t == 'array'                   ): return False
    if (len(self.args)   > 1  and cmd == 'set'                   ): return False
    return True

  def validate_set_cmd(self):
    if(len(self.args) == 1 and self.args[0] == "temp"):
      for target, data in self.kwargs.iteritems():
        if (not type(data) in [float, int]): self.err('temp_value')
      return

    if(len(self.args) != 0): self.err('args_err')

    for k, v in self.kwargs.iteritems():
      original_v = v
      if (type(v) == bool): original_v = {True: "on", False: "off"}[v]
      namespaces = ["fan", "conveyor", "motors"]
      if (not k in namespaces): self.err('set_key', k, original_v)
      if (k == "motors" and type(v) != bool): self.err('motor_value', k)
      if (not type(v) in [bool, float, int]): self.err('f_or_c_value', k)

  def err(self, key, *args):
    if key == 'args_err': args = [self._cmds[self.cmd]['args_error']]
    raise Exception(self._errors[key](*args))

  _errors = dict(
    args_err      = lambda err: err,
    cmd_not_found = lambda cmd:
      "%s command does not exist."%cmd,
    temp_value    = lambda:
      "temperature values must be numeric (ex: set temp e0: 10)",
    set_key       = lambda k, v:
      "invalid key '%s'. Did you mean 'set temp %s: %s'?"%(k,k, v),
    motors_value  = lambda:
      "set %s accepts either on or off (ex: set motors: on)",
    f_or_c_value  = lambda k:
      "set %s accepts a numeric speed, on or off (ex: set %s 255)"%(k,k)
  )

  _cmds = {
    "home": {
      'type': "home",
      'args_error': "home only (optionally) accepts axe names (ex: home x)."
    },
    "move": {
      'type': "dict",
      'args_error': textwrap.dedent("""
        move takes a list of axes, distance pairs and optionally a @ 
        prefixed feedrate multiplier (ex: move x: 20 @ 200%).
      """).strip()
    },
    "set": {
      'type': None,
      'args_error': textwrap.dedent("""
        set accepts only the following parameters:
          1. the word "temp" followed by a list of heater, value 
          pairs (ex: set temp e0: 220)
          2. one of [motor|fan|conveyor] followed a collon and "on", "off" or a 
          numeric speed for fans and conveyors (ex: set fan: 255).
      """).strip()
    },
    "estop": {
      'type': "none",
      'args_error': "estop does not require any parameters."
    },
    "print": {
      'type': "none",
      'args_error': "print does not require any parameters."
    },
    "rm_job": {
      'type': "array",
      'args_error': "rm_job accepts a job id (ex: rm_job 5)."
    },
    "change_job": {
      'type': "dict",
      'args_error': textwrap.dedent("""
        change_job accepts a list of key/value pairs which must include an id 
        (ex: change_job id: 5, position: 0).
      """).strip()
    },
    "get_jobs": {
      'type': "none",
      'args_error': "get_jobs does not require any parameters."
    },
    "raw": {
      'type': "array",
      'args_error': textwrap.dedent("""
        accepts raw commands and passes them on (ex: "raw G28" will pass on the home all axis command)
      """).strip()
    },
  }
