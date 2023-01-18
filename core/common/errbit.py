# pylint: disable-all
import logging
import socket
import sys
import threading
import traceback
from xml.sax.saxutils import escape

from django.conf import settings

from core.common.utils import get_request_url

app_name = 'OCLAPI2'
version = settings.VERSION


ERRBIT_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<notice version="2.4">'
    '<api-key>{api_key}</api-key>'
    '<notifier>'
    '<name>OCL API2</name>'
    '<version>{version}</version>'
    '</notifier>'
    '<framework>Djangov2</framework>'
    '<error>'
    '<class>{class_name}</class>'
    '<message><![CDATA[{value}]]></message>'
    '<backtrace>{trace}</backtrace>'
    '</error>'
    '<request>'
    '<component>{component}</component>'
    '<url>{url}</url>'
    '<cgi-data></cgi-data>'
    '<params>'
    '<var key="foobar">verify</var>'
    '<var key="controller">application</var>'
    '</params>'
    '</request>'
    '<server-environment>'
    '<project-root>/code</project-root>'
    '<environment-name>{environment}</environment-name>'
    '</server-environment>'
    '<current-user>'
    '<name>{user}</name>'
    '</current-user>'
    '</notice>'
)

ERRBIT_TRACE = '<line number="{line_number}" file="{filename}" method="{function_name}: {text}"/>'


def log_error(method):
    def wrap_error(*args, **kwargs):
        try:
            if len(kwargs):
                method(**kwargs)
            else:
                method(*args)
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.setLevel(logging.ERROR)
            logger.exception(e)

    wrap_error.__name__ = method.__name__
    return wrap_error


class ThreadedRequest(threading.Thread):
    def __init__(self, url, message, headers):
        super(ThreadedRequest, self).__init__()
        self.url = url
        self.message = message
        self.headers = headers

    @log_error
    def run(self):
        from urllib.request import urlopen
        import urllib.error
        try:
            response = urlopen(self.url, self.message, 20)
            status = response.getcode()
        except urllib.error.HTTPError as e:
            status = e.code
        except urllib.error.URLError:
            return

        if status == 200:
            return

        exception_message = "Unexpected status code {0}".format(str(status))
        if status == 403:
            exception_message = "Unable to send using SSL"
        elif status == 422:
            exception_message = "Invalid XML sent"
        elif status == 500:
            exception_message = "Destination server is unavailable. Please check the remote server status."
        elif status == 503:
            exception_message = "Service unavailable. You may be over your quota."

        print("MESSAGE: ", self.message)
        print("ERRBIT_EXCEPTION: ", exception_message)


class ErrbitClient:
    def __init__(self, service_url, api_key, component, node, environment):
        self.service_url = service_url
        self.api_key = api_key
        self.component_name = component
        self.node_name = node
        self.environment = environment

    def raise_errbit(self, message):
        try:
            raise Exception(message)
        except Exception as ex:  # pylint: disable=broad-except
            self.log(ex)

    @log_error
    def log(self, exception):
        message = self.generate_xml(exception, sys.exc_info()[2])
        self.send_message(message.encode('utf-8'))

    def send_request(self, headers, message):
        t = ThreadedRequest(self.service_url, message, headers)
        t.start()

    def send_message(self, message):
        headers = {"Content-Type": "text/xml"}
        self.send_request(headers, message)

    def generate_xml(self, exc, trace):
        return self.xml_raw(exc, exc.args, trace)

    @staticmethod
    def _trace(trace):
        _trace_str = ''
        for filename, line_number, function_name, text in traceback.extract_tb(trace):
            function_name = function_name.replace('<', '').replace('>', '').replace('"', "'")
            text = text.replace('<', '').replace('>', '').replace('"', "'")
            _trace_str += ERRBIT_TRACE.format(
                line_number=str(line_number), filename=filename, function_name=function_name, text=text
            )
        return _trace_str

    def xml_raw(self, etype, value, trace, limit=None, file=None):
        from .utils import get_current_user
        _trace_str = ''
        cause = None
        if value and value.__cause__:
            cause = value.__cause__
            _trace_str += self._trace(cause.__traceback__)
            _trace_str += '<line method="The above exception was the direct cause of the following exception:"/>'
        _trace_str += self._trace(trace)
        message_value = str(value)
        if cause:
            message_value += ' from ' + str(cause)
        return ERRBIT_XML.format(
            api_key=self.api_key, version=settings.VERSION, class_name=etype.__class__.__name__, value=message_value,
            trace=_trace_str, component=self.component_name, environment=self.environment, user=str(get_current_user()),
            url=escape(str(get_request_url()))
        )


ERRBIT_LOGGER = ErrbitClient(
    settings.ERRBIT_URL + '/notifier_api/v2/notices',
    settings.ERRBIT_KEY,
    component="root",
    node=socket.gethostname(),
    environment=settings.ENV
)

original_print_exception = traceback.print_exception


def print_exception_with_errbit_logging(etype, value, tb, limit=None, file=None):
    if not (etype == KeyError and str(value) == "'cid'"):
        message = ERRBIT_LOGGER.xml_raw(etype, value, tb)
        ERRBIT_LOGGER.send_message(message.encode('utf-8'))
        original_print_exception(etype, value, tb, limit=None, file=None)


traceback.print_exception = print_exception_with_errbit_logging
