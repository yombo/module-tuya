try:  # Prefer simplejson if installed, otherwise json will work swell.
    import simplejson as json
except ImportError:
    import json

from twisted.internet.defer import inlineCallbacks

from yombo.lib.webinterface.auth import require_auth
from yombo.core.log import get_logger

logger = get_logger("modules.tuya.web_routes")

def module_tuya_routes(webapp):
    """
    Adds routes to the webinterface module.

    :param webapp: A pointer to the webapp, it's used to setup routes.
    :return:
    """
    with webapp.subroute("/module_settings") as webapp:

        def root_breadcrumb(webinterface, request):
            webinterface.add_breadcrumb(request, "/?", "Home")
            webinterface.add_breadcrumb(request, "/modules/index", "Modules")
            webinterface.add_breadcrumb(request, "/module_settings/tuya/index", "Jinvoo")

        @webapp.route("/tuya", methods=['GET'])
        @require_auth()
        def page_module_tuya_get(webinterface, request, session):
            return webinterface.redirect(request, '/module_settings/tuya/index')

        @webapp.route("/tuya/index", methods=['GET'])
        @require_auth()
        def page_tools_module_tuya_index_get(webinterface, request, session):
            # tuya = webinterface._Modules['Jinvoo']

            page = webinterface.webapp.templates.get_template('modules/tuya/web/index.html')
            root_breadcrumb(webinterface, request)
            return page.render(alerts=webinterface.get_alerts(),
                               )
