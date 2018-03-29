try:  # Prefer simplejson if installed, otherwise json will work swell.
    import simplejson as json
except ImportError:
    import json

from twisted.internet.defer import inlineCallbacks

from yombo.lib.webinterface.auth import require_auth
from yombo.core.log import get_logger

logger = get_logger("modules.zwave.web_routes")

def module_zwave_routes(webapp):
    """
    Adds routes to the webinterface module.

    :param webapp: A pointer to the webapp, it's used to setup routes.
    :return:
    """
    with webapp.subroute("/module_settings") as webapp:

        def root_breadcrumb(webinterface, request):
            webinterface.add_breadcrumb(request, "/?", "Home")
            webinterface.add_breadcrumb(request, "/modules/index", "Modules")
            webinterface.add_breadcrumb(request, "/module_settings/jinvoo/index", "Jinvoo")

        @webapp.route("/jinvoo", methods=['GET'])
        @require_auth()
        def page_module_zwave_get(webinterface, request, session):
            return webinterface.redirect(request, '/module_settings/zwave/index')

        @webapp.route("/jinvoo/index", methods=['GET'])
        @require_auth()
        def page_tools_module_zwave_index_get(webinterface, request, session):
            zwave = webinterface._Modules['ZWave']

            page = webinterface.webapp.templates.get_template('modules/jinvoo/web/index.html')
            root_breadcrumb(webinterface, request)
            return page.render(alerts=webinterface.get_alerts(),
                               )
