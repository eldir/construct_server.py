import traceback, time, tornado, os
from event_emitter import EventEmitter

class PrintJobQueue(EventEmitter):

  def __init__(self, server):
    super(PrintJobQueue, self).__init__()
    self.list = []
    self.__last_id = 0
    self.previous_job_progress = 0
    self.current_job = None
    self.server = server
    self.printer = server.printer

  def do_get_jobs(self):
    jobexport = []
    if self.current_job != None:
      jobexport.append(
        dict(
          id = self.current_job["id"],
          file_name = self.current_job["file_name"],
          printing = True
        )
      )
    jobexport.extend(self.public_list())
    return {'jobs': jobexport}

  def public_list(self):
    # A sanitized version of list for public consumption via construct
    l2 = []
    for job in self.list:
      l2.append(self.sanitize(job))
    return l2

  def sanitize(self, job):
    return dict(
      id = job["id"],
      file_name = job["file_name"],
      printing = (job == self.current_job)
    )

  def do_add_job(self, file_name, body):
    ext = os.path.splitext(file_name)[1]
    job = dict(
      id = self.__last_id,
      file_name=file_name,
      body= body,
    )
    self.__last_id += 1

    self.list.append(job)
    print "Added %s"%(file_name)
    self.fire("job_added", self.sanitize(job))

  def display_summary(self):
    print "Print Jobs:"
    for job in self.list:
      print "  %i: %s"%(job['id'], job['file_name'])
    print ""
    return True

  def do_rm_job(self, job_id):
    job = self.find_by_id(job_id)
    if job == None:
      return False
    self.list.remove(job)
    print "Print Job Removed"
    self.fire("job_removed", self.sanitize(job))

  def do_change_job(self, **job_attrs):
    job = self.find_by_id(job_attrs["id"])
    # proposed future print quantity functionality
    # if hasattr(job_attrs, 'qty'): job['qty'] = qty
    if job_attrs['position']:
      position = int(job_attrs['position'])
      self.list.remove(job)
      self.list.insert(position, job)
    print int(job_attrs['position'])
    print "Print #%s Job Updated ( %s )."%(job['id'], job['file_name'])
    self.fire("job_updated", self.sanitize(job))

  def find_by_id(self, job_id):
    try:
      job_id = int(job_id)
    except:
      raise Exception("job_id must be a number")
    for job in self.list:
      if job['id'] == job_id: return job
    raise Exception("There is no job #%i."%job_id)

  def fire(self, event_name, content):
    super(PrintJobQueue, self).fire(event_name, content)

  def iterate_print_job_loop(self):
    # This is a polling work around to the current lack of events in printcore
    # A better solution would be one in which a print_finised event could be 
    # listend for asynchronously without polling.
    try:
      if self.server.status == "printing" and self.printer.is_printing():
        if self.current_job != None:
          self.update_job_progress(progress=100)
          self.fire("job_finished", self.sanitize(self.current_job))
          print "Print job complete."

        self.display_summary()
        pause_between_prints = self.server.c_settings.pause_between_prints
        if pause_between_prints and self.current_job != None:
          print "Pausing between jobs."
          self.current_job = None
          self.server.set_status("idle")
        elif len(self.list) > 0:
          print "Starting the next print job"
          self.current_job = self.list.pop(0)
          self.printer.start_print_job(self.current_job)
          self.fire("job_started", self.sanitize(self.current_job))
        else:
          print "Finished all print jobs"
          self.current_job = None
          self.server.set_status("idle")

      # Updating the job progress
      self.update_job_progress()

    except Exception as ex:
      print traceback.format_exc()

  def update_job_progress(self, progress=None):
    if progress == None: progress = self.printer.print_progress()
    if progress != self.previous_job_progress:
      self.previous_job_progress = progress
      self.fire("job_progress_changed", progress)
