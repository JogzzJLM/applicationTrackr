"""
Web server facade for ApplicationTrackr.
Re-exports web handler and view functions maintaining 100% backward compatibility.
"""
from web.views import render_unified_dashboard_html
from web.handlers import ThreadedHTTPServer, CleanHandler, start_web_server
from web.components import render_job_card