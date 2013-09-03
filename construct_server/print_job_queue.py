import traceback, time, tornado, os
from event_emitter import EventEmitter

class PrintJobQueue(EventEmitter):

  def __init__(self, server):
    super(PrintJobQueue, self).__init__()
    self.list = []
    self.current_job = None
    self.server = server
    self.printer = server.printer
    self.__next_id = 0

  def public_list(self):
    # A sanitized version of list for public consumption via construct
    list = self.list
    if not self.current_job == None: list = [self.current_job] + list
    return  [self.sanitize(job) for job in list]

  def sanitize(self, job):
    whitelist = ['id', 'file_name', 'status', 'total_lines', 'current_line']
    return {k:v for k,v in job.iteritems() if k in whitelist}

  def display_summary(self):
    print "Print Jobs:"
    for job in self.list: print "  %i: %s"%(job['id'], job['file_name'])
    print ""

  def do_get_jobs(self):
    return dict(jobs= self.public_list())

  def do_add_job(self, file_name, body):
    ext = os.path.splitext(file_name)[1]
    job = dict(
      id = self.__next_id,
      file_name = file_name,
      body = body,
      position = self.list.__len__(),
      #position = lambda: self.list.index(job),
      total_lines = self.printer.total_print_lines(body),
      current_line = 0,
      status = 'queued',
      type = "job"
    )
    self.__next_id += 1
    self.list.append(job)
    self.server.c_add(['jobs', job['id']], job, internal= True)
    print "Added %s"%(file_name)

  def do_rm_job(self, job_id):
    job = self.server.c_get(['jobs', int(job_id)])
    if job['status'] in ["printing", "finished"]:
      raise Exception("Cannot remove a %s job"%job['status'])
    self.list.remove(job)
    self.server.c_rm(['jobs', job['id']])
    print "Print Job Removed"

  def on_job_position_change(self, parent_path, job, position):
    self.list.remove(job)
    self.list.insert(position, job)
    print position
    print "Print #%s Job Updated ( %s )."%(job['id'], job['file_name'])

  # proposed future print quantity functionality:
  # def on_job_qty_change(self, job, qty):

  def iterate_print_job_loop(self):
    # This is a polling work around to the current lack of events in printcore
    # A better solution would be one in which a print_finised event could be 
    # listend for asynchronously without polling.
    if self.server.c_get(['status'])=="printing" and self.printer.is_printing():
      if self.current_job != None:
        self.update_job_progress(line=self.current_job.total_lines)
        self.server.c_set([self.current_job.id, "status"], "finished")
        print "Print job complete."

      pause_between_prints = self.server.c_get(['pause_between_prints'])
      no_job = self.current_job != None
      if (pause_between_prints and no_job) or len(self.list) == 0:
        self.current_job = None
        self.server.c_set(['status'], "idle")
      elif len(self.list) > 0:
        print "Starting the next print job"
        self.current_job = self.list.pop(0)
        self.printer.start_print_job(self.current_job)
        self.server.c_set(['jobs', self.current_job.id, 'status'], "printing")
      self.display_summary()

    # Updating the job progress
    self.update_job_progress()

  def update_job_progress(self, line=None):
    if self.current_job == None: return
    if line == None: line = self.printer.current_print_line
    self.server.c_set(['jobs', self.current_job.id, 'current_line'], line)
