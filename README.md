# construct-server-python

A Python Construct Protocol server library.

## The Construct Server

\_\_init\_\_ takes the following kwargs:

- settings: A dict containing
  - sensor\_poll\_rate
  - sensor\_names
- server_settings: A dict of tornado application settings
- routes: An array of tornado routes to append to the standard construct routes
- components: A dict of any applicable printer components keyed by their type
  and stored as arrays of component `short_name`s including:
  - extruders
  - beds
  - fans
  - conveyors
  - axes

## The Printer Interface

### Methods

A printer must have the following methods defined:

#### def is_online(self):

#### def is\_printing(self):

#### def request\_sensor\_update(self):

#### def post\_process\_print\_job(self, filename, filebody):

The returned object will be stored as the print job's body.

#### def start\_print\_job(self, job):

#### def current\_print\_line(self):

#### def total\_print\_lines(self, job_body):

### Events

- 

