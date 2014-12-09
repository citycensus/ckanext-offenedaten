from pylons.i18n import _
import os

from sqlalchemy import distinct, func

import ckan.lib.helpers as h
from ckan.lib.helpers import json
from ckan.lib.base import BaseController, c, g, request, \
                          response, render, config, abort, redirect
from ckan import model
from ckan.model import Session, PackageExtra
import ckan.logic as logic

import logging
log = logging.getLogger(__name__)

from datetime import datetime
import gdata.spreadsheet.text_db

get_action = logic.get_action


def get_root_dir():
    here = os.path.dirname(__file__)
    rootdir = os.path.dirname(os.path.dirname(here))
    return rootdir


#class RewiringController(BaseController):
#
#    def tag(self, tags):
#        redirect(h.url_for(controller='package', action='search', tags=tags))


class SubscribeController(BaseController):
    '''
        Stores the email address provided by the user in a Google Docs
        Spreadsheet. The spreadsheet connection parameters must be defined
        in the configuration file:
            * offenedaten.gdocs.username
            * offenedaten.gdocs.password
            * offenedaten.gdocs.dockey
            * offenedaten.gdocs.sheet [Optional, defaults to 'Sheet1'

        The spreadhsheet must have two header fields named 'email' and
        'signedup'

    '''
    def __before__(self):
        super(SubscribeController, self).__before__(self)

        # Check Google Docs parameters
        username = config.get('offenedaten.gdocs.username', None)
        password = config.get('offenedaten.gdocs.password', None)
        dockey = config.get('offenedaten.gdocs.dockey', None)
        sheet = config.get('offenedaten.gdocs.sheet', 'Sheet1')

        if not username or not password or not dockey:
            log.error('Google Docs connection settings not specified')
            abort(500)

        # Setup connection
        self.client = gdata.spreadsheet.text_db.DatabaseClient(
            username=username, password=password)
        db = self.client.GetDatabases(dockey)[0]
        self.table = db.GetTables(name=sheet)[0]
        self.table.LookupFields()

    def send(self):
        if not 'email' in request.params:
            abort(400, _('Please provide an email address'))
        email = request.params['email']
        row = {'email': email, 'signedup': datetime.now().isoformat()}
        self.table.AddRecord(row)
        h.flash_success(_(
            'Your email has been stored. Thank you for your interest.'))
        redirect('/')

class MapController(BaseController):

    def _get_config(self):
        c.startColor = config.get('offenedaten.map.start_color', '#FFFFFF')
        c.endColor = config.get('offenedaten.map.end_color', '#045A8D')
        c.num_groups = config.get('offenedaten.map.groups', 5)

    def index(self):
        self._get_config()

        # package search
        context = {'model': model, 'session': model.Session,
                   'user': c.user or c.author, 'for_view': True}
        data_dict = {
            'q': '*:*',
            'facet.field': g.facets,
            'rows': 0,
            'start': 0,
        }
        query = logic.get_action('package_search')(context, data_dict)
        c.package_count = query['count']
        c.facets = query['facets']
        c.search_facets = query['search_facets']

        # Add the featured related applications to the template context.
        data_dict = {
            'type_filter': 'application',
            'featured': True,
        }
        c.feautured_related_apps = logic.get_action('related_list')(context,
            data_dict)

        # Add the featured related ideas to the template context.
        data_dict = {
            'type_filter': 'idea',
            'featured': True,
        }
        c.feautured_related_ideas = logic.get_action('related_list')(context,
            data_dict)

        return render('home/index.html')

    def _compactresults(self, item):
        notinextras = ('name', 'title', 'package_count')
        inextras = ('url', 'latitude', 'longitude', 'polygon', 'city_type', 'contact_email', 'open_data_portal')
        ritem = {}
        for key in notinextras:
            ritem[key] = item[key]
        for akey in item['extras']:
            if akey['key'] in inextras:
                ritem[akey['key']] = akey['value']
        return ritem  

    def show(self):
        self._get_config()
        #This needs to get all orgs
        #Do it based on the data because we want to be able to take params and filter
        #But for now just orgs
        group_type = 'organization'

        context = {'model': model, 'session': model.Session,
                   'user': c.user or c.author, 'for_view': True,
                   'with_private': False}
                   
        q = c.q = request.params.get('q', '')
        data_dict = {'all_fields': True, 'q': q, 'include_extras': True}
        sort_by = c.sort_by_selected = request.params.get('sort')
        if sort_by:
            data_dict['sort'] = sort_by
            
        #TODO: Get last modified date
        #TODO: Get %open
        results = get_action('organization_list')(context, data_dict)
        passresults = map(self._compactresults, results)
        c.results = json.dumps(passresults)
        
        return render('home/map.html')     

    def data(self):
        # Get the Europe dataset
        rootdir = get_root_dir()
        data_file = os.path.join(rootdir, 'ckanext', 'offenedaten', 'data', 'eu.json')
        f = open(data_file, 'r')
        o = json.load(f)

        # Get the package count by country
        q = Session.query(
                distinct(PackageExtra.value),
                func.count(PackageExtra.value)
            ).\
                filter(PackageExtra.key == u'eu_country').\
                group_by(PackageExtra.value)

        values = dict(q.all())
        # Set the package count for each country
        
        for ft in o['features']:
            code = ft['properties']['NUTS']
            ft['properties']['packages'] = (values.get(code, 0))

        response.content_type = 'application/json'
        response.pragma = None
        response.cache_control = 'public; max-age: 3600'
        response.cache_expires(seconds=3600)
        return json.dumps(o)
