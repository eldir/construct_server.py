class EventEmitter(object):
  def __init__(self):
    self.listeners = set()

  def fire(self, event_name, *args):
    callback_name = "on_%s" % event_name
    for listener in self.listeners:
      if hasattr(listener, callback_name):
        callback = getattr(listener, callback_name)
        callback(*args)
      elif hasattr(listener, "on_uncaught_event"):
        listener.on_uncaught_event(event_name, content)
      else:
        continue
