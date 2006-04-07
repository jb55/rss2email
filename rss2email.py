#!/usr/bin/python
"""rss2email: get RSS feeds emailed to you
http://www.aaronsw.com/2002/rss2email

Usage:
  new [emailaddress] (create new feedfile)
  email newemailaddress (update default email)
  run [--no-send] [num]
  add feedurl [emailaddress]
  list
  delete n
"""
__version__ = "2.56"
__author__ = "Aaron Swartz (me@aaronsw.com)"
__copyright__ = "(C) 2004 Aaron Swartz. GNU GPL 2."
___contributors__ = ["Dean Jackson", "Brian Lalor", "Joey Hess", 
                     "Matej Cepl", "Martin 'Joey' Schulze", 
                     "Marcel Ackermann (http://www.DreamFlasher.de)", 
                     "Lindsey Smith (lindsey.smith@gmail.com)" ]

### Vaguely Customizable Options ###

# The email address messages are from by default:
DEFAULT_FROM = "bozo@dev.null.invalid"

# 1: Send text/html messages when possible.
# 0: Convert HTML to plain text.
HTML_MAIL = 0

# 1: Only use the DEFAULT_FROM address.
# 0: Use the email address specified by the feed, when possible.
FORCE_FROM = 0

# 1: Receive one email per post.
# 0: Receive an email every time a post changes.
TRUST_GUID = 1

# 1: Generate Date header based on item's date, when possible.
# 0: Generate Date header based on time sent.
DATE_HEADER = 0

# A tuple consisting of some combination of
# ('issued', 'created', 'modified', 'expired')
# expressing ordered list of preference in dates 
# to use for the Date header of the email.
DATE_HEADER_ORDER = ('modified', 'issued', 'created')

# 1: Apply Q-P conversion (required for some MUAs).
# 0: Send message in 8-bits.
# http://cr.yp.to/smtp/8bitmime.html
QP_REQUIRED = 0

# 1: Name feeds as they're being processed.
# 0: Keep quiet.
VERBOSE = 0

# 1: Use the publisher's email if you can't find the author's.
# 0: Just use the DEFAULT_FROM email instead.
USE_PUBLISHER_EMAIL = 0

# 1: Use SMTP_SERVER to send mail.
# 0: Call /usr/sbin/sendmail to send mail.
SMTP_SEND = 0

SMTP_SERVER = "smtp.yourisp.net:25"
AUTHREQUIRED = 0 # if you need to use SMTP AUTH set to 1
SMTP_USER = 'username'  # for SMTP AUTH, set SMTP username here
SMTP_PASS = 'password'  # for SMTP AUTH, set SMTP password here

# Set this to add a bonus header to all emails (start with '\n').
BONUS_HEADER = ''
# Example: BONUS_HEADER = '\nApproved: joe@bob.org'

# Set this to override From addresses. Keys are feed URLs, values are new titles.
OVERRIDE_FROM = {}

# Note: You can also override the send function.
def send(fr, to, message, smtpserver=None):
	if SMTP_SEND:
		if not smtpserver: 
			import smtplib; 
			try:
				smtpserver = smtplib.SMTP(SMTP_SERVER)
			except KeyboardInterrupt:
				raise
			except Exception, e:
				print >>warn, ""
				print >>warn, ('Fatal error: could not connect to mail server "%s"' % SMTP_SERVER)
				if hasattr(e, 'reason'):
					print >>warn, "Reason:", e.reason
				sys.exit(1)
					
			if AUTHREQUIRED:
				try:
					smtpserver.login(SMTP_USER, SMTP_PASS)
				except KeyboardInterrupt:
					raise
				except Exception, e:
					print >>warn, ""
					print >>warn, ('Fatal error: could not authenticate with mail server "%s" as user "%s"' % (SMTP_SERVER, SMTP_USER))
					if hasattr(e, 'reason'):
						print >>warn, "Reason:", e.reason
					sys.exit(1)
					
		smtpserver.sendmail(fr, [to], message)	
		return smtpserver
	else:
 		i, o = os.popen2(["/usr/sbin/sendmail", to])
 		i.write(message)
 		i.close(); o.close()
 		del i, o
 		return None

## html2text options ##

# Use Unicode characters instead of their ascii psuedo-replacements
UNICODE_SNOB = 0

# Put the links after each paragraph instead of at the end.
LINKS_EACH_PARAGRAPH = 0

# Wrap long lines at position. 0 for no wrapping. (Requires Python 2.3.)
BODY_WIDTH = 0

### Load the Options ###

# Read options from config file if present.
import sys
sys.path.append(".")
try:
	from config import *
except:
	pass
	
### Import Modules ###

import cPickle as pickle, md5, time, os, traceback, urllib2, sys, types
unix = 0
try:
	import fcntl
	unix = 1
except:
	pass
		
import socket; socket_errors = []
for e in ['error', 'gaierror']:
	if hasattr(socket, e): socket_errors.append(getattr(socket, e))
import mimify; from StringIO import StringIO as SIO; mimify.CHARSET = 'utf-8'

import feedparser
feedparser.USER_AGENT = "rss2email/"+__version__+ " +http://www.aaronsw.com/2002/rss2email/"

import html2text as h2t

h2t.UNICODE_SNOB = UNICODE_SNOB
h2t.LINKS_EACH_PARAGRAPH = LINKS_EACH_PARAGRAPH
h2t.BODY_WIDTH = BODY_WIDTH
html2text = h2t.html2text

### Utility Functions ###

warn = sys.stderr

def isstr(f): return isinstance(f, type('')) or isinstance(f, type(u''))
def ishtml(t): return type(t) is type(())
def contains(a,b): return a.find(b) != -1
def unu(s): # I / freakin' hate / that unicode
	if type(s) is types.UnicodeType: return s.encode('utf-8')
	else: return s

def quote822(s):
	"""Quote names in email according to RFC822."""
	return '"' + unu(s).replace("\\", "\\\\").replace('"', '\\"') + '"'

def header7bit(s):
	"""QP_CORRUPT headers."""
	#return mimify.mime_encode_header(s + ' ')[:-1]
	# XXX due to mime_encode_header bug
	import re
	p = re.compile('=\n([^ \t])');
	return p.sub(r'\1', mimify.mime_encode_header(s + ' ')[:-1])

### Parsing Utilities ###

def getContent(entry, HTMLOK=0):
	"""Select the best content from an entry, deHTMLizing if necessary.
	If raw HTML is best, an ('HTML', best) tuple is returned. """
	
	# How this works:
	#  * We have a bunch of potential contents. 
	#  * We go thru looking for our first choice. 
	#    (HTML or text, depending on HTMLOK)
	#  * If that doesn't work, we go thru looking for our second choice.
	#  * If that still doesn't work, we just take the first one.
	#
	# Possible future improvement:
	#  * Instead of just taking the first one
	#    pick the one in the "best" language.
	#  * HACK: hardcoded HTMLOK, should take a tuple of media types
	
	conts = entry.get('content', [])
	
	if entry.get('summary_detail', {}):
		conts += [entry.summary_detail]
	
	if conts:
		if HTMLOK:
			for c in conts:
				if contains(c.type, 'html'): return ('HTML', c.value)
	
		for c in conts:
			if c.type == 'text/plain': return c.value
	
		if not HTMLOK: # Only need to convert to text if HTML isn't OK
			for c in conts:
				if contains(c.type, 'html'):
					return html2text(c.value)
		
		return conts[0].value	
	
	return ""

def getID(entry):
	"""Get best ID from an entry."""
	if TRUST_GUID:
		if 'id' in entry and entry.id: return entry.id

	content = getContent(entry)
	if content: return md5.new(unu(content)).hexdigest()
	if 'link' in entry: return entry.link
	if 'title' in entry: return md5.new(unu(entry.title)).hexdigest()

def getName(r, entry):
	"""Get the best name."""

	feed = r.feed
	if r.url in OVERRIDE_FROM.keys():
		return unu(OVERRIDE_FROM[r.url])
	
	name = feed.get('title', '')
	
	if 'name' in entry.get('author_detail', []): # normally {} but py2.1
		if entry.author_detail.name:
			if name: name += ", "
			name +=  entry.author_detail.name

	elif 'name' in feed.get('author_detail', []):
		if feed.author_detail.name:
			if name: name += ", "
			name += feed.author_detail.name
	
	return name

def getEmail(feed, entry):
	"""Get the best email_address."""

	if FORCE_FROM: return DEFAULT_FROM
	
	if 'email' in entry.get('author_detail', []):
		return entry.author_detail.email
	
	if 'email' in feed.get('author_detail', []):
		return feed.author_detail.email
		
	#TODO: contributors
	
	if USE_PUBLISHER_EMAIL:
		if 'email' in feed.get('publisher_detail', []):
			return feed.publisher_detail.email
		
		if feed.get("errorreportsto", ''):
			return feed.errorreportsto
			
	return DEFAULT_FROM

### Simple Database of Feeds ###

class Feed:
	def __init__(self, url, to):
		self.url, self.etag, self.modified, self.seen = url, None, None, {}
		self.to = to		

def load(lock=1):
	if not os.path.exists(feedfile):
		print 'Feedfile "%s" does not exist.  If you\'re using r2e for the first time, you' % feedfile
		print "have to run 'r2e new' first."
		sys.exit(1)
	try:
		feedfileObject = open(feedfile, 'r')
	except IOError, e:
		print "Feedfile could not be opened: %s" % e
		sys.exit(1)
	feeds = pickle.load(feedfileObject)
	if lock:
		if unix: fcntl.flock(feedfileObject.fileno(), fcntl.LOCK_EX)
		#HACK: to deal with lock caching
		feedfileObject = open(feedfile, 'r')
		feeds = pickle.load(feedfileObject)
		if unix: fcntl.flock(feedfileObject.fileno(), fcntl.LOCK_EX)

	return feeds, feedfileObject

def unlock(feeds, feedfileObject):
	if not unix: 
		pickle.dump(feeds, open(feedfile, 'w'))
	else:	
		pickle.dump(feeds, open(feedfile+'.tmp', 'w'))
		os.rename(feedfile+'.tmp', feedfile)
		fcntl.flock(feedfileObject.fileno(), fcntl.LOCK_UN)

### Program Functions ###

def add(*args):
	if len(args) == 2 and contains(args[1], '@') and not contains(args[1], '://'):
		urls, to = [args[0]], args[1]
	else:
		urls, to = args, None
	
	feeds, feedfileObject = load()
	if (feeds and not isstr(feeds[0]) and to is None) or (not len(feeds) and to is None):
		print "No email address has been defined. Please run 'r2e email emailaddress' or"
		print "'r2e add url emailaddress'."
		sys.exit(1)
	for url in urls: feeds.append(Feed(url, to))
	unlock(feeds, feedfileObject)

def run(num=None):
	feeds, feedfileObject = load()
	try:
		# We store the default to address as the first item in the feeds list.
		# Here we take it out and save it for later.
		default_to = ""
		if feeds and isstr(feeds[0]): default_to = feeds[0]; ifeeds = feeds[1:] 
		else: ifeeds = feeds
		
		if num: ifeeds = [feeds[num]]
		
		smtpserver = None
		
		for f in ifeeds:
			try: 
				if VERBOSE: print >>warn, "I: Processing", f.url
				r = feedparser.parse(f.url, f.etag, f.modified)
				
				# Handle various status conditions, as required
				if 'status' in r:
					if r.status == 301: f.url = r['url']
					elif r.status == 410:
						print >>warn, "W: feed gone; deleting", f.url
						feeds.remove(f)
						continue
				
				http_status = r.get('status', 200)
				http_headers = r.get('headers', {
				  'content-type': 'application/rss+xml', 
				  'content-length':'1'})
				exc_type = r.get("bozo_exception", Exception()).__class__
				if http_status != 304 and not r.entries and not r.get('version', ''):
					if http_status not in [200, 302]: 
						print >>warn, "W: error", http_status, f.url

					elif contains(http_headers.get('content-type', 'rss'), 'html'):
						print >>warn, "W: looks like HTML", f.url

					elif http_headers.get('content-length', '1') == '0':
						print >>warn, "W: empty page", f.url

					elif hasattr(socket, 'timeout') and exc_type == socket.timeout:
						print >>warn, "W: timed out on", f.url
					
					elif exc_type == IOError:
						print >>warn, "W:", r.bozo_exception, f.url
					
					elif hasattr(feedparser, 'zlib') and exc_type == feedparser.zlib.error:
						print >>warn, "W: broken compression", f.url
					
					elif exc_type in socket_errors:
						exc_reason = r.bozo_exception.args[1]
						print >>warn, "W:", exc_reason, f.url

					elif exc_type == urllib2.URLError:
						if r.bozo_exception.reason.__class__ in socket_errors:
							exc_reason = r.bozo_exception.reason.args[1]
						else:
							exc_reason = r.bozo_exception.reason
						print >>warn, "W:", exc_reason, f.url
					
					elif exc_type == AttributeError:
						print >>warn, "W:", r.bozo_exception, f.url
					
					elif exc_type == KeyboardInterrupt:
						raise r.bozo_exception

					else:
						print >>warn, "=== SEND THE FOLLOWING TO rss2email@aaronsw.com ==="
						print >>warn, "E:", r.get("bozo_exception", "can't process"), f.url
						print >>warn, r
						print >>warn, "rss2email", __version__
						print >>warn, "feedparser", feedparser.__version__
						print >>warn, "html2text", h2t.__version__
						print >>warn, "Python", sys.version
						print >>warn, "=== END HERE ==="
					continue
				
				r.entries.reverse()
				
				for entry in r.entries:
					id = getID(entry)
					
					# If TRUST_GUID isn't set, we get back hashes of the content.
					# Instead of letting these run wild, we put them in context
					# by associating them with the actual ID (if it exists).
					
					frameid = entry.get('id', id)
					
					# If this item's ID is in our database
					# then it's already been sent
					# and we don't need to do anything more.
					
					if f.seen.has_key(frameid) and f.seen[frameid] == id: continue

					if not (f.to or default_to):
						print "No default email address defined. Please run 'r2e email emailaddress'"
						print "Ignoring feed %s" % f.url
						break
					
					if 'title_detail' in entry and entry.title_detail:
						title = entry.title_detail.value
						if contains(entry.title_detail.type, 'html'):
							title = html2text(title)
					else:
						title = getContent(entry)[:70]

					title = unu(title).replace("\n", " ")
					
					datetime = time.gmtime()

					if DATE_HEADER:
						for datetype in DATE_HEADER_ORDER:
							kind = datetype+"_parsed"
							if kind in entry and entry[kind]: datetime = entry[kind]
						
					content = getContent(entry, HTMLOK=HTML_MAIL)
					
					link = unu(entry.get('link', ""))
					
					from_addr = unu(getEmail(r.feed, entry))

					message = (
					"From: " + quote822(header7bit(getName(r, entry))) + " <"+from_addr+">" +
					"\nTo: " + header7bit(unu(f.to or default_to)) + # set a default email!
					"\nSubject: " + unu(html2text(header7bit(title))).strip() +
					"\nDate: " + time.strftime("%a, %d %b %Y %H:%M:%S -0000", datetime) +
					"\nUser-Agent: rss2email" + # really should be X-Mailer 
					BONUS_HEADER +
					"\nContent-Type: ")         # but backwards-compatibility
					
					if ishtml(content):
						message += "text/html"
						
						content = ("<html><body>\n\n" + 
						           '<h1><a href="'+link+'">'+title+'</a></h1>\n\n' +
						           unu(content[1]).strip() + # drop type tag (HACK: bad abstraction)
						           '<p>URL: <a href="'+link+'">'+link+'</a></p>' +
						           "\n\n</body></html>")
					else:
						message += "text/plain"
						content = unu(content).strip() + "\n\nURL: "+link
						
					message += '; charset="utf-8"\n\n' + content + "\n"

					if QP_REQUIRED:
						ins, outs = SIO(message), SIO()
						mimify.mimify(ins, outs)
						message = outs.getvalue()
						
					smtpserver = send(from_addr, (f.to or default_to), message, smtpserver)
			
					f.seen[frameid] = id
					
				f.etag, f.modified = r.get('etag', None), r.get('modified', None)
			except (KeyboardInterrupt, SystemExit):
				raise
			except:
				print >>warn, "=== SEND THE FOLLOWING TO rss2email@aaronsw.com ==="
				print >>warn, "E: could not parse", f.url
				traceback.print_exc(file=warn)
				print >>warn, "rss2email", __version__
				print >>warn, "feedparser", feedparser.__version__
				print >>warn, "html2text", h2t.__version__
				print >>warn, "Python", sys.version
				print >>warn, "=== END HERE ==="
				continue

	finally:		
		unlock(feeds, feedfileObject)
		if smtpserver:
			smtpserver.quit()

def list():
	feeds, feedfileObject = load(lock=0)
	default_to = ""
	
	if feeds and isstr(feeds[0]):
		default_to = feeds[0]; ifeeds = feeds[1:]; i=1
		print "default email:", default_to
	else: ifeeds = feeds; i = 0
	for f in ifeeds:
		print `i`+':', f.url, '('+(f.to or ('default: '+default_to))+')'
		if not (f.to or default_to):
			print "   W: Please define a default address with 'r2e email emailaddress'"
		i+= 1

def delete(n):
	feeds, feedfileObject = load()
	if n == 0:
		print >>warn, "W: ID has to be equal to or higher than 1"
	elif n >= len(feeds):
		print >>warn, "W: no such feed"
	else:
		feeds = feeds[:n] + feeds[n+1:]
		if n != len(feeds):
			print >>warn, "W: feed IDs have changed, list before deleting again"
	unlock(feeds, feedfileObject)
	
def email(addr):
	feeds, feedfileObject = load()
	if feeds and isstr(feeds[0]): feeds[0] = addr
	else: feeds = [addr] + feeds
	unlock(feeds, feedfileObject)

if __name__ == '__main__':
	ie, args = "InputError", sys.argv
	try:
		if len(args) < 3: raise ie, "insufficient args"
		feedfile, action, args = args[1], args[2], args[3:]
		
		if action == "run": 
			if args and args[0] == "--no-send":
				def send(x,y,z,w=None):
					if VERBOSE: print 'Not sending', ([x for x in z.splitlines() if x.startswith("Subject:")][0])

			if args and args[-1].isdigit(): run(int(args[-1]))
			else: run()

		elif action == "email":
			if not args:
				raise ie, "Action '%s' requires an argument" % action
			else:
				email(args[0])

		elif action == "add": add(*args)

		elif action == "new": 
			if len(args) == 1: d = [args[0]]
			else: d = []
			pickle.dump(d, open(feedfile, 'w'))

		elif action == "list": list()

		elif action in ("help", "--help", "-h"): print __doc__

		elif action == "delete":
			if not args:
				raise ie, "Action '%s' requires an argument" % action
			elif args[0].isdigit():
				delete(int(args[0]))
			else:
				raise ie, "Action '%s' requires a number as its argument" % action

		else:
			raise ie, "invalid action"
		
	except ie, e:
		print "E:", e
		print
		print __doc__



