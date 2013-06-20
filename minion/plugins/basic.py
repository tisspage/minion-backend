# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.


import collections
import logging
import os
import re
import time
import sys
import urlparse

from twisted.internet.task import LoopingCall
from robots_scanner.scanner import scan

import minion.curly
from minion.plugins.base import AbstractPlugin,BlockingPlugin,ExternalProcessPlugin

#
# AlivePlugin
#

class AlivePlugin(BlockingPlugin):

    """
    This plugin checks if the site is alive or not. If any error occurs, the whole plan
    will be aborted. This is useful to have as the first plugin in a workflow. Anything
    non-200 will be seen as a fatal error.
    """

    PLUGIN_NAME = "Alive"
    PLUGIN_WEIGHT = "light"
    FURTHER_INFO = [ { 
        "URL": "http://www.w3.org/Protocols/rfc2616/rfc2616-sec10.html",
        "Title": "W3C - Status Code Definitions" } ],

    REPORTS = {
        "good": 
            {
                "Summary": "Site is reachable",
                "Description": "The server has responded with {status_code} status_code. \
This indicates the site is reachable.",
                "Severity": "Info",
                "URLs": [ {"URL": None, "Extra": None} ],
                "FurtherInfo": FURTHER_INFO,
             },
        "bad": 
            {
                "Summary": "Site could not be reached",
                "Description": None,
                "Severity": "Fatal",
                "URLs": [ { "URL": None, "Extra": None} ],
                "FurtherInfo": FURTHER_INFO,
            }
    }            

    def do_run(self):
        try:
            r = minion.curly.get(self.configuration['target'], connect_timeout=5, timeout=15)
            r.raise_for_status()
            issue = self._format_report('good', description_formats={'status_code': str(r.status)})
            self.report_issue(issue)
        except minion.curly.BadResponseError as error:
            issue = self._format_report('bad', description=str(error))
            self.report_issue(issue)
            return AbstractPlugin.EXIT_STATE_ABORTED
#       
# XFrameOptionsPlugin
#

class XFrameOptionsPlugin(BlockingPlugin):

    """
    This is a minimal plugin that does one http request to find out if
    the X-Frame-Options header has been set. It does not override anything
    except start() since that one check is quick and there is no point
    in suspending/resuming/terminating.

    All plugins run in a separate process so we can safely do a blocking
    HTTP request. The PluginRunner catches exceptions thrown by start() and
    will report that back as an error state of the plugin.
    """

    PLUGIN_NAME = "XFrameOptions"
    PLUGIN_WEIGHT = "light"

    FURTHER_INFO = [ { 
        "URL": "https://developer.mozilla.org/en-US/docs/HTTP/X-Frame-Options",
        "Title": "Mozilla Developer Network - The X-Frame-Options response header" }]

    REPORTS = {
        "set": 
            {
                "Summary": "X-Frame-Options header is set properly",
                "Description": "Site has the following X-Frame-Options set: {header}",
                "Severity": "Info",
                "URLs": [ {"URL": None, "Extra": None} ],
                "FurtherInfo": FURTHER_INFO
             },
        "invalid": 
            {
                "Summary": "Invalid X-Frame-Options header detected",
                "Description": "The following X-Frame-Options header value is detected and is invalid: {header}",
                "Severity": "High",
                "URLs": [ { "URL": None, "Title": None} ],
                "FurtherInfo": FURTHER_INFO
            },
        "not-set":
            {
                "Summary": "X-Frame-Options header is not set",
                "Description": "X-Frame-Options header is not found. Sites can use this to avoid clickjacking attacks, \
by ensuring that their content is not embedded into other sites.",
                "Severity": "High",
                "URLs": [ { "URL": None, "Title": None} ],
                "FurtherInfo": FURTHER_INFO
            },
            
    }            
    def _allow_from_validator(self, value):
        """ Only accept the following basic forms::
        ACCEPT-FROM http://example.org[:port]/[path]
        ACCEPT-FORM https://example.org[:port]/[path]
        
        Reject those with colon, or uri containing query and/or
        fragement.

        For details, please refer to https://bugzilla.mozilla.org/show_bug.cgi?id=836132#c28
        """
        # for simplicity, keep everything uppercase
        value = value.upper()
        # reject allow-from with colon
        regex = re.compile("ALLOW-FORM:")
        matches = regex.findall(value)
        if matches:
            return False
        # verify url is present and valid
        regex = re.compile(r'(?P<tag>ALLOW-FROM)\s(?P<url>.+)')
        matches = regex.match(value)
        if not matches:
            return False
        url = matches.group('url')
        if url:
            scheme, domain, path, query, fragement = urlparse.urlsplit(url)
            if query or fragement:
                return False
            elif not scheme in ('http', 'https'):
                return False
            return True

    def do_run(self):
        r = minion.curly.get(self.configuration['target'], connect_timeout=5, timeout=15)
        r.raise_for_status()
        if 'x-frame-options' in r.headers:
            xfo_value = r.headers['x-frame-options']
            # 'DENY' and 'SAMEORIGIN' don't carry extra values
            if xfo_value.upper() in ('DENY', 'SAMEORIGIN'):
                issue = self._format_report('set', description_formats={'header': xfo_value})
                self.report_issues([issue])
            # only strict ALLOW-FROM syntax is allowed
            elif 'ALLOW-FROM' in xfo_value.upper():
                if self._allow_from_validator(xfo_value):
                    issue = self._format_report('set', description_formats={'header': xfo_value})
                    self.report_issues([issue])
                else:
                    issue = self._format_report('invalid', description_formats={'header': xfo_value})
                    self.report_issues([issue])
           # found invalid/unknown option value         
            else:
                issue = self._format_report('invalid', description_formats={'header': xfo_value})
                self.report_issues([issue])
        else:
            issue = self._format('not-set')
            self.report_issues([issue])


class HSTSPlugin(BlockingPlugin):

    """
    This plugin checks if the site sends out an HSTS header if it is HTTPS enabled.
    """

    PLUGIN_NAME = "HSTS"
    PLUGIN_WEIGHT = "light"

    FURTHER_INFO = [ { 
        "URL": "https://developer.mozilla.org/en-US/docs/Security/HTTP_Strict_Transport_Security",
        "Title": "Mozilla Developer Network - HTTP Strict Transport Security" }]

    REPORTS = {
        "set": 
            {
                "Summary": "Strict-Transport-Security header is set properly",
                "Description": "Site has the following Strict-Transport-Security header set: {header}",
                "Severity": "Info",
                "URLs": [ {"URL": None, "Extra": None} ],
                "FurtherInfo": FURTHER_INFO
             },
        "invalid": 
            {
                "Summary": "Invalid Strict-Transport-Security header detected",
                "Description": "The following Strict-Transport-Security header value is detected and is invalid: {header}",
                "Severity": "High",
                "URLs": [ { "URL": None, "Title": None} ],
                "FurtherInfo": FURTHER_INFO
            },
        "not-set":
            {
                "Summary": "Strict-Transport-Security header is not set",
                "Description": "Strict-Transport-Security header is not found. This header is a security feature that \
lets a web site tell browsers that it should only be communicated with using HTTPS, instead of using HTTP.",
                "Severity": "High",
                "URLs": [ { "URL": None, "Title": None} ],
                "FurtherInfo": FURTHER_INFO
            },
            
    }            

    def do_run(self):
        r = minion.curly.get(self.configuration['target'], connect_timeout=5, timeout=15)
        r.raise_for_status()
        if r.url.startswith("https://"):
            if 'strict-transport-security' not in r.headers:
                issues = self._format_report('not-set')
                self.report_issues([issues])
            else:
                issues = self._format_report('set', description_formats={'header': r.headers['strict-transport-security']})
                self.report_issues([issues])


class XContentTypeOptionsPlugin(BlockingPlugin):

    """
    This plugin checks if the site sends out a X-Content-Type-Options header
    """

    PLUGIN_NAME = "XContentTypeOptions"
    PLUGIN_WEIGHT = "light"

    FURTHER_INFO = [ { 
        "URL": "http://msdn.microsoft.com/en-us/library/ie/gg622941%28v=vs.85%29.aspx",
        "Title": "MIME-Handling Change: X-Content-Type-Options: nosniff" }]

    REPORTS = {
        "set": 
            {
                "Summary": "X-Content-Type-Options is set properly",
                "Description": "Site has the following X-Content-Type-Options header set: {header}",
                "Severity": "Info",
                "URLs": [ {"URL": None, "Extra": None} ],
                "FurtherInfo": FURTHER_INFO
             },
        "invalid": 
            {
                "Summary": "Invalid X-Content-Type-Options header detected",
                "Description": "The following X-Content-Type-Options header value is detected and is invalid: {header}",
                "Severity": "High",
                "URLs": [ { "URL": None, "Title": None} ],
                "FurtherInfo": FURTHER_INFO
            },
        "not-set":
            {
                "Summary": "X-Content-Type-Options header is not set",
                "Description": "X-Content-Type-Options header is not found. This header is a security feature that helps \
prevent attacks based on MIME-type confusion.",
                "Severity": "High",
                "URLs": [ { "URL": None, "Title": None} ],
                "FurtherInfo": FURTHER_INFO
            },
            
    }            

    def do_run(self):
        r = minion.curly.get(self.configuration['target'], connect_timeout=5, timeout=15)
        r.raise_for_status()
        value = r.headers.get('x-content-type-options')
        if not value:
            self.report_issues([self._format_report('not-set')])
        else:
            if value.lower() == 'nosniff':
                issue = self._format_report('set', description_formats={'header': value})
                self.report_issues([issue])
            else:
                issue = self._format_report('invalid', description_formats={'header': value})
                self.report_issues([issue])

class XXSSProtectionPlugin(BlockingPlugin):

    """
    This plugin checks if the site sends out a X-XSS-Protection header
    """

    PLUGIN_NAME = "XXSSProtection"
    PLUGIN_WEIGHT = "light"

    FURTHER_INFO = [ { 
        "URL": "http://blogs.msdn.com/b/ie/archive/2008/07/02/ie8-security-part-iv-the-xss-filter.aspx",
        "Title": "IE8 Security Part IV: The XSS Filter" }]

    REPORTS = {
        "set": 
            {
                "Summary": "X-XSS-Protection is set properly",
                "Description": "Site has the following X-XSS-Protection header set: {header}",
                "Severity": "Info",
                "URLs": [ {"URL": None, "Extra": None} ],
                "FurtherInfo": FURTHER_INFO
             },
        "invalid": 
            {
                "Summary": "Invalid X-XSS-Protection header detected",
                "Description": "The following X-XSS-Protection header value is detected and is invalid: {header}",
                "Severity": "High",
                "URLs": [ { "URL": None, "Title": None} ],
                "FurtherInfo": FURTHER_INFO
            },
        "not-set":
            {
                "Summary": "X-XSS-Protection header is not set",
                "Description": "X-XSS-Protection header is not found. \
This header enables Cross-site scripting (XSS) filter built into most recent web browsers.",
                "Severity": "High",
                "URLs": [ { "URL": None, "Title": None} ],
                "FurtherInfo": FURTHER_INFO
            },    
        "disabled":
            {
                "Summary": "X-XSS-Protection header is set to disable",
                "Description": "X-XSS-Protection header is set to 0 and consequent disabled Cross-site-scripting (XSS) filter.",
                "Severity": "High",
                "URLs": [ { "URL": None, "Title": None} ],
                "FurtherInfo": FURTHER_INFO
            },
    }            

    def do_run(self):
        r = minion.curly.get(self.configuration['target'], connect_timeout=5, timeout=15)
        r.raise_for_status()
        value = r.headers.get('x-xss-protection')
        if not value:
            self.report_issues([self._format_report('not-set')])
        else:
            if value.lower() == '1; mode=block':
                self.report_issues([self._format_report('set', description_formats={'header': value})])
            elif value == '0':
                self.report_issues([self._format_report('disabled', description_formats={'header': value})])
            else:
                self.report_issues([self._format_report('invalid', description_formats={'header': value})])


class ServerDetailsPlugin(BlockingPlugin):

    """
    This plugin checks if the site sends out a Server or X-Powered-By header that exposes details about the server software.
    """
    
    PLUGIN_NAME = "ServerDetails"
    PLUGIN_WEIGHT = "light"

    FURTHER_INFO = [ 
        { 
            "URL": "http://tools.ietf.org/html/rfc2616#section-14.38",
            "Title": 'RFC 2616 - "Server" header'
        },
        {
            "URL": "https://developer.mozilla.org/en-US/docs/HTTP/Headers",
            "Title": "Mozilla Developer Network - HTTP Headers"
        },
        {
            "URL": "https://en.wikipedia.org/wiki/List_of_HTTP_header_fields",
            "Title": "Wikipedia - List of HTTP header fields",
        }
]

    REPORTS = {
        "set": 
            {
                "Summary": "",
                "Description": "Site has set {header} header",
                "Severity": "Medium",
                "URLs": [ {"URL": None, "Extra": None} ],
                "FurtherInfo": FURTHER_INFO
             },
         "none":
         {
             "Summary": "No server-detail-type headers set",
             "Description": "None of the following headers is present: {headers}",
             "Severity": "Info",
             "URLs": [ {"URL": None, "Extra": None} ],
             "FurtherInfo": FURTHER_INFO
         }
    }            

    def do_run(self):
        r = minion.curly.get(self.configuration['target'], connect_timeout=5, timeout=15)
        r.raise_for_status()
        headers = ('Server', 'X-Powered-By', 'X-AspNet-Version', 'X-AspNetMvc-Version', 'X-Backend-Server')
        at_least_one = False
        for header in headers:
            if header.lower() in r.headers:
                at_least_one = True
                issue = self._format_report('set', description_formats={'header': header})
                issue['Summary'] = "%s is found" % header
                self.report_issues([issue])
        if not at_least_one:
            self.report_issues([self._format_report('none', description_formats={'headers': headers})])

class RobotsPlugin(BlockingPlugin):
    
    """
    This plugin checks if the site has a robots.txt.
    """

    PLUGIN_NAME = "Robots"
    PLUGIN_WEIGHT = "light"

    def validator(self, url):
        """ This validator performs the following checkes:

        1. Invalidate the scan if HTTP status code is not 200,
        2. Invalidate the scan if HTTP content-type header
        is not set to 'text/plain',
        3. Invalidate the scan if robots_scanner.scanner.scan
        finds 'Disallow:' appears before 'User-agent:' does at
        the beginning of the document.

        Known enhancement to be made:
        1. should limit the size of robots.txt acceptable by our 
        scanner
        2. use more optimized regex
        """

        url_p = urlparse.urlparse(url)
        url = url_p.scheme + '://' + url_p.netloc + '/robots.txt'
        resp = minion.curly.get(url, connect_timeout=5, timeout=15)
        if resp.status != 200:
            return 'NOT-FOUND'
        if 'text/plain' not in resp.headers['content-type'].lower():
            return False
        try:
            if not scan(resp.body):
                return False
            return True
        except Exception:
            return False

    def do_run(self):
        result = self.validator(self.configuration['target'])
        if result == 'NOT-FOUND':
            self.report_issues([{"Summary":"No robots.txt found", "Severity": "Medium"}])
        elif not result:
            self.report_issues([{"Summary":"Invalid robots.txt found", "Severity": "Medium"}])
        

#
# CSPPlugin
#        
class CSPPlugin(BlockingPlugin):

    """
    This plugin checks if a CSP header is set.
    """

    PLUGIN_NAME = "CSP"
    PLUGIN_WEIGHT = "light"

    def _extract_csp_header(self, headers, keys_tuple):
        keys = set(headers)
        matches = keys.intersection(keys_tuple)
        name = None
        value = None
        if len(matches) == 2:
            name = t[0]
            value = headers[name]
        elif matches:
            name = matches.pop()
            value = headers[name]
        return name, value


    def _parse_csp(self, csp):
        # adopted from Django
        _url_regex = re.compile(
            r'((?:http|ftp)s?://|\*.)*'  # http:// or https://
            r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?))'  # domain...
            r'(?::\d+)?'  # optional port
            r'(?:/?|[/?]\S+)$', re.IGNORECASE)

        options = collections.defaultdict(list)
        p = re.compile(r';\s*')
        for rule in p.split(csp):
            a = rule.split()
            if a:
                values = a[1:]
                for value in values:
                    if value in ("'none'", "*"):
                        if len(values) > 2:
                            raise ValueError("When %s is present, other values cannot co-exist with %s" %(value, value))
                    elif value not in ("'self'", "'unsafe-inline'", "'unsafe-eval'", "'https:'", "'https'"):
                        if _url_regex.match(value) is None:
                            raise ValueError("%s does not seem like a valid uri for %s" % (value, a[0]))
                    elif value == "'unsafe-inline'":
                        self.report_issues([{"Summary":"CSP Rules allow unsafe-inline", "Severity":"High"}])
                    elif value == "'unsafe-eval'":
                        self.report_issues([{"Summary":"CSP Rules allow unsafe-eval", "Severity":"High"}])
                options[a[0]] += a[1:]
        return options

    def do_run(self):
        GOOD_HEADERS = ('x-content-security-policy', 'content-security-policy',)
        BAD_HEADERS = ('x-content-security-policy-report-only', \
                'content-security-policy-report-only',)
        r = minion.curly.get(self.configuration['target'], connect_timeout=5, timeout=15)
        r.raise_for_status()

        csp_hname, csp = self._extract_csp_header(r.headers, GOOD_HEADERS)
        csp_ro_name, csp_report_only = self._extract_csp_header(r.headers, BAD_HEADERS)

        # Fast fail if both headers are set
        if csp and csp_report_only:
            self.report_issues([{"Summary":"Both %s and %s headers set" %(csp_hname, csp_ro_name), "Severity": "High"}])
            return

        # Fast fail if only reporting is enabled
        if csp_report_only:
            self.report_issues([{"Summary":"%s header set" % csp_ro_name, "Severity": "High"}])
            return

        # Fast fail if no CSP header is set
        if csp is None:
            self.report_issues([{"Summary":"No Content-Security-Policy header set", "Severity": "High"}])
            return

        # Parse the CSP and look for issues
        try:
            csp_config = self._parse_csp(csp)
            if not csp_config:
                self.report_issues([{"Summary":"Malformed %s header set" % csp_hname, "Severity":"High"}])
                return
            # Allowing eval-script or inline-script defeats the purpose of CSP?
            csp_options = csp_config.get('options')
            if csp_options:
                if 'eval-script' in csp_config['options']:
                    self.report_issues([{"Summary":"CSP Rules allow eval-script", "Severity":"High"}])
                if 'inline-script' in csp_config['options']:
                    self.report_issues([{"Summary":"CSP Rules allow inline-script", "Severity":"High"}])
        except ValueError as e:
                self.report_issues([{"Summary":"Malformed %s header set: %s" %(csp_hname, e), "Severity":"High"}])
                
