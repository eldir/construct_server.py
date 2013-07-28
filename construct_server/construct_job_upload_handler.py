import tornado, uuid
from cStringIO import StringIO
from construct_auth import construct_auth

@tornado.web.stream_body
class ConstructJobUploadHandler(tornado.web.RequestHandler):

  def prepare(self):
    construct_auth(self, None)

  def post(self):
    self.read_bytes = 0
    self.total_bytes = self.request.content_length
    self.file_str = StringIO()

    session_uuid = self.get_argument("session_uuid", None)
    self.websocket = None
    print session_uuid
    if session_uuid in self.application.clients:
      self.websocket = self.application.clients[session_uuid]
    self.request.request_continue()
    self.read_chunks()

  def read_chunks(self, chunk=''):
    self.read_bytes += len(chunk)
    self.file_str.write(chunk)
    if chunk: self.process_chunk()

    chunk_length = min(100000, self.request.content_length - self.read_bytes)
    if chunk_length > 0:
      self.request.connection.stream.read_bytes(chunk_length, self.read_chunks)
    else:
      self.request._on_request_body(self.file_str.getvalue(), self.uploaded)

  def process_chunk(self):
    # print self.get_argument("session_uuid", None)
    print "bytes: (%i / %i)"%(self.read_bytes, self.total_bytes)
    msg = {'uploaded': self.read_bytes, 'total': self.total_bytes}
    if self.websocket != None:
      self.websocket.send([dict(type="job_upload_progress_changed", data= msg)])

  def uploaded(self):
    printer = self.application.printer
    fileinfo = self.request.files['job'][0]
    body = fileinfo['body']
    if hasattr(printer, "post_process_print_job"):
      body = printer.post_process_print_job(fileinfo['filename'], body)
    self.application.jobs.do_add_job(fileinfo['filename'], body)

    self.finish("ACK")
