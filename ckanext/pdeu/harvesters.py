#coding: utf-8
import urllib2,urllib,urlparse
import string
import re
from datetime import datetime

import logging

from ckan.logic.action.create import package_create_rest
from ckan.logic.action.update import package_update_rest
from ckan.logic.action.get import package_show
from ckan.logic.schema import default_package_schema
from ckan.logic import ValidationError, NotFound
from ckan import model
from ckan.model import Session,Package
from ckan.lib.navl.validators import ignore_missing
from ckan.lib.munge import munge_title_to_name
from ckan.lib.helpers import json

from ckan.plugins.core import SingletonPlugin, implements

from ckanext.harvest.interfaces import IHarvester
from ckanext.harvest.model import HarvestJob, HarvestObject, HarvestGatherError, \
                                    HarvestObjectError

from lxml import html,etree
from cookielib import CookieJar

log = logging.getLogger(__name__)

class PDEUHarvester(SingletonPlugin):
    '''
    Generic class for publicdata.eu harvesters
    '''
    implements(IHarvester)

    def _gen_new_name(self,title):
        name = munge_title_to_name(title).replace('_', '-')
        while '--' in name:
            name = name.replace('--', '-')
        like_q = u'%s%%' % name
        pkg_query = Session.query(Package).filter(Package.name.ilike(like_q)).limit(100)
        taken = [pkg.name for pkg in pkg_query]
        if name not in taken:
            return name
        else:
            counter = 1
            while counter < 101:
                if name+str(counter) not in taken:
                    return name+str(counter)
                counter = counter + 1
            return None

    def _get_content(self, url):
        http_request = urllib2.Request(url=url)
        try:
            http_response = urllib2.urlopen(http_request)
            return http_response.read()
        except Exception, e:
            raise e

    def _save_gather_error(self,message,job):
        err = HarvestGatherError(message=message,job=job)
        err.save()
        log.error(message)

    def _save_object_error(self,message,obj,stage=u'Fetch'):
        err = HarvestObjectError(message=message,object=obj,stage=stage)
        err.save()
        log.error(message)

    def _create_harvest_objects(self, remote_ids, harvest_job):
        try:
            object_ids = []
            if len(remote_ids):
                for remote_id in remote_ids:
                    # Create a new HarvestObject for this identifier
                    obj = HarvestObject(guid = remote_id, job = harvest_job)
                    obj.save()
                    object_ids.append(obj.id)
                return object_ids
            else:
               self._save_gather_error('No remote datasets could be identified', harvest_job)
        except Exception, e:
            self._save_gather_error('%r' % e.message, harvest_job)

    def _create_or_update_package(self, package_dict, harvest_object):
        '''
        Creates a new package or updates an exisiting one according to the
        package dictionary provided. The package dictionary should look like
        the REST API response for a package:

        http://ckan.net/api/rest/package/statistics-catalunya

        Note that the package_dict must contain an id, which will be used to
        check if the package needs to be created or updated (use the remote
        dataset id).

        If the remote server provides the modification date of the remote
        package, add it to package_dict['metadata_modified'].

        '''
        try:
            #from pprint import pprint 
            #pprint(package_dict)
            ## change default schema
            schema = default_package_schema()
            schema["id"] = [ignore_missing, unicode]

            context = {
                'model': model,
                'session': Session,
                'user': u'harvest',
                'api_version':'2',
                'schema': schema,
            }

            # Check if package exists
            context.update({'id':package_dict['id']})
            try:
                existing_package_dict = package_show(context)
                # Check modified date
                if not 'metadata_modified' in package_dict or \
                   package_dict['metadata_modified'] > existing_package_dict.get('metadata_modified'):
                    log.info('Package with GUID %s exists and needs to be updated' % harvest_object.guid)
                    # Update package
                    updated_package = package_update_rest(package_dict, context)

                    harvest_object.package_id = updated_package['id']
                    harvest_object.save()
                else:
                    log.info('Package with GUID %s not updated, skipping...' % harvest_object.guid)

            except NotFound:
                # Package needs to be created
                del context['id']
                log.info('Package with GUID %s does not exist, let\'s create it' % harvest_object.guid)
                new_package = package_create_rest(package_dict, context)
                harvest_object.package_id = new_package['id']
                harvest_object.save()

            return True

        except ValidationError,e:
            log.exception(e)
            self._save_object_error('Invalid package with GUID %s: %r'%(harvest_object.guid,e.error_dict),harvest_object,'Import')
        except Exception, e:
            log.exception(e)
            self._save_object_error('%r'%e,harvest_object,'Import')

        return None


class DataPublicaHarvester(PDEUHarvester):
    INITIAL_INDEX = "http://www.data-publica.com/en/data/WebSection_viewContentDetailledList"
    INDEX_URL = "http://www.data-publica.com/en/data"

    def info(self):
        return {
            'name': 'data_publica',
            'title': 'Data Publica',
            'description': 'Scraper for data-publica.com'
        }

    gathered_ids = []

    page = 1

    def _gather_ids(self,url = None, jar= None):
        log.debug('Page %s'%self.page)
        if jar is None:
            jar = CookieJar()
        opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(jar))
        url = url or self.INITIAL_INDEX
        fh = opener.open(url)
        doc = html.parse(fh)
        fh.close()

        new_ids = []
        for a in doc.findall(".//div[@class='main']//a"):
            href = a.get('href').split('?', 1)[0]
            id = href.split('/').pop()
            if not id in self.gathered_ids:
                log.debug('Got Id: %s' % id)
                #self.queue(DataPublicaDatasetCrawler, url=href)
                new_ids.append(id)

        if len(new_ids) == 0: # or self.page == 2:
            return self.gathered_ids
        else:
            self.gathered_ids.extend(new_ids)

        inputs = []
        for input in doc.findall(".//form[@id='main_form']//input"):
            inputs.append((input.get('name'), input.get('value')))
        inputs.append(('listbox_nextPage:method', ''))
        next_url = self.INDEX_URL + '?' + urllib.urlencode(inputs)
        self.page = self.page + 1
        return self._gather_ids(url=next_url,jar=jar)

    def gather_stage(self,harvest_job):
        log.debug('In DataPublica gather_stage (%s)' % harvest_job.source.url)

        remote_ids = self._gather_ids(self.INITIAL_INDEX)
        #remote_ids = ['20110524-36F426','20110524-10821AB','20110523-10DACE3']

        return self._create_harvest_objects(remote_ids,harvest_job)


    def fetch_stage(self,harvest_object):
        log.debug('In DataPublicaHarvester fetch_stage')
        # Get URL
        url = harvest_object.source.url.rstrip('/')
        url = url + '/en/data_set_module/' + harvest_object.guid

        # Get contents
        try:
            content = self._get_content(url)
        except Exception,e:
            log.exception(e)
            self._save_object_error('Unable to get content for dataset: %s: %r' % \
                                        (url, e),harvest_object)
            return None

        # Save the fetched contents in the HarvestObject
        harvest_object.content = content
        harvest_object.save()

        return True

    def import_stage(self,harvest_object):

        log.debug('In DataPublicaHarvester import_stage')
        if not harvest_object:
            log.error('No harvest object received')
            return False

        if harvest_object.content is None:
            self._save_object_error('Empty content for object %s' % harvest_object.id,harvest_object,'Import')
            return False
        try:
            package_dict = {}
            extras_dict = {}

            #TODO: Avoid collisions?
            package_dict['id'] = harvest_object.guid
            doc = html.document_fromstring(harvest_object.content)
            for field in doc.findall(".//div"):
                if not 'field' in field.get('class', ''): continue
                name = field.find("label").text.strip()

                if name == 'Title':
                    package_dict['title'] = field.find("div").xpath("string()").strip()

                if name == 'Categories':
                    extras_dict['categories'] = []
                    for elem in field.findall("div[@class='input']"):
                        if not elem.text: continue
                        extras_dict['categories'].append(elem.text.strip())

                if name == 'Software Licence':
                    #TODO: what to do with these?
                    a = field.find("div/a")
                    if a is not None:
                        extras_dict['license_url'] = a.get('href')
                        extras_dict['licence'] = a.text.strip()

                if name == 'Editor':
                    a = field.find("div/a")
                    if a is not None:
                        package_dict['author'] = a.text.strip()

                if name == 'Deposit Date':
                    text = field.find("div[@class='input']").xpath("string()")
                    text = "".join([c for c in text if c in string.digits+"/:"])
                    if len(text.strip()):
                        extras_dict['deposit_date'] = datetime.strptime(text, "%d/%m/%Y%H:%M").isoformat()

                if name == 'Update Date':
                    text = field.find("div[@class='input']").xpath("string()")
                    text = "".join([c for c in text if c in string.digits+"/:"])
                    if len(text.strip()):
                        extras_dict['update_date'] = datetime.strptime(text, "%d/%m/%Y%H:%M").isoformat()

                if name == 'Frequency Update':
                    text = field.find("div[@class='input']").xpath("string()")
                    extras_dict['frequency_update'] = text.strip()

                if name == 'Tags':
                    package_dict['tags'] = []
                    for elem in field.find("div[@class='input']/div").iter():
                        tag = None
                        if elem.text:
                            tag = elem.text.strip()
                        if elem.tail:
                            tag = elem.tail.strip()
                        if tag:
                            # "Sanitize" tags
                            tag = re.sub(r'[^a-zA-Z0-9 ]','',tag).replace(' ','-').lower()
                            package_dict['tags'].append(tag)

                if name == 'Description':
                    text = field.find("div[@class='input']/div").xpath("string()")
                    package_dict['notes'] = text.strip()

                if name == 'URL':
                    # This should link to the orginal URL
                    package_dict['url'] = field.find("div/a").get('href')

                #FIELD Data Publications
                if name == 'Data Publications':
                    package_dict['resources'] = []
                    resource_descriptions = [a.text.strip() for a in field.findall(".//div[@class='data']/div[@class='main']//a")]
                    resource_formats = [a.text.strip() for a in field.findall(".//div[@class='data']/div[@class='second']//a")]
                    resource_links = [a.get('href') for a in field.findall(".//div[@class='icon']//a")]
                    for i in range(len(resource_links)):
                        package_dict['resources'].append({
                            'url':resource_links[i],
                            'format':resource_formats[i],
                            'description':resource_descriptions[i]
                            })
            

            # Common extras
            extras_dict['harvest_catalogue_name'] = u'Data Publica'
            extras_dict['harvest_catalogue_url'] = u'http://www.data-publica.com'
            extras_dict['harvest_dataset_url'] = u'http://www.data-publica.com/en/data_set_module/%s' % harvest_object.guid
            extras_dict['eu_country'] = u'FR'

            package_dict['name'] = self._gen_new_name(package_dict['title'])
            package_dict['extras'] = extras_dict

        except Exception, e:
            log.exception(e)
            self._save_object_error('%r'%e,harvest_object,'Import')

        return self._create_or_update_package(package_dict,harvest_object)


from ckanext.rdf.consume import consume_one
from ckanext.rdf.vocab import Graph
try: from cStringIO import StringIO
except ImportError: from StringIO import StringIO

class OpenGovSeHarvester(PDEUHarvester):
    INDEX_URL = "http://www.opengov.se/feeds/data/"
    ATOM_NS = "http://www.w3.org/2005/Atom"

    def info(self):
        return {
            'name': 'opengov_se',
            'title': 'OpenGov.se',
            'description': 'Harvester for opengov.se'
        }

    def gather_stage(self,harvest_job):
        log.debug('In OpenGovSeHarvester gahter_stage')
        # Get feed contents
        doc = etree.parse(self.INDEX_URL)
        remote_ids = []
        for id_element in doc.findall('//{%(ns)s}entry/{%(ns)s}id' % {'ns':self.ATOM_NS}):
            id = id_element.text.strip()
            log.debug('Got id: %s' % id)
            remote_ids.append(id)

        return self._create_harvest_objects(remote_ids,harvest_job)

    def fetch_stage(self,harvest_object):
        log.debug('In OpenGovSeHarvester fetch_stage')

        # Get URL
        url = harvest_object.guid.strip('/') + '/rdf/'

        # Get contents
        try:
            content = self._get_content(url)
        except Exception,e:
            self._save_object_error('Unable to get content for dataset: %s: %r' % \
                                        (url, e), harvest_object)
            return None

        # Save the fetched contents in the HarvestObject
        harvest_object.content = content
        harvest_object.save()
        return True

    def import_stage(self,harvest_object):
        log.debug('In OpenGovSeHarvester import_stage')
        if not harvest_object:
            log.error('No harvest object received')
            return False

        if harvest_object.content is None:
            self._save_object_error('Empty content for object %s' % harvest_object.id,harvest_object,'Import')
            return False

        try:
            graph = Graph()
            graph.parse(StringIO(harvest_object.content))

            url = harvest_object.guid
            package_dict = consume_one(graph)
            tags = []
            for tag in package_dict.get('tags', []):
                tag = re.sub(r'[^a-zA-Z0-9 ]','',tag).replace(' ','-').lower()
                tags.append(tag)
            package_dict['tags'] = tags

        except Exception, e:
            log.exception(e)
            self._save_object_error('%r'%e,harvest_object,'Import')

        package_dict['id'] = harvest_object.guid
        if not package_dict['name']:
            package_dict['name'] = self._gen_new_name(package_dict['title'])

        # Set the modification date
        if 'date_modified' in package_dict['extras']:
            package_dict['metadata_modified'] = package_dict['extras']['date_modified']

        # Common extras
        package_dict['extras']['harvest_catalogue_name'] = u'Opengov.se'
        package_dict['extras']['harvest_catalogue_url'] = u'http://www.opengov.se'
        package_dict['extras']['harvest_dataset_url'] = harvest_object.guid
        package_dict['extras']['eu_country'] = u'SE'

        return self._create_or_update_package(package_dict,harvest_object)

import json
from csv import DictReader
class DataLondonGovUkHarvester(PDEUHarvester):
    CATALOGUE_CSV_URL = "http://data.london.gov.uk/datafiles/datastore-catalogue.csv"

    def info(self):
        return {
            'name': 'data_london_gov_uk',
            'title': 'data.london.gov.uk',
            'description': 'CSV Import from GLA Datastore'
        }

    def gather_stage(self, harvest_job):
        log.debug('In DataLondonGovUk gather_stage')
        
        csvfh = urllib2.urlopen(self.CATALOGUE_CSV_URL)
        csv = DictReader(csvfh)
        ids = []
        for row in csv:
            id = row.get('DRUPAL_NODE')
            row = dict([(k, v.decode('latin-1')) for k, v in row.items()])
            obj = HarvestObject(guid=id, job=harvest_job,
                    content=json.dumps(row))
            obj.save()
            ids.append(obj.id)
        return ids

    def fetch_stage(self, harvest_object):
        return True

    def import_stage(self,harvest_object):
        if not harvest_object:
            log.error('No harvest object received')
            return False

        if harvest_object.content is None:
            self._save_object_error('Empty content for object %s' % harvest_object.id,harvest_object,'Import')
            return False

        try:
            row = json.loads(harvest_object.content)
            def csplit(txt):
                return [t.strip() for t in txt.split(",")]

            package_dict = {
                    'title': row['TITLE'],
                    'url': row['URL'],
                    'notes': row['LONGDESC'],
                    'author': row['AUTHOR_NAME'],
                    'maintainer': row['MAINTAINER'],
                    'maintainer_email': row['MAINTAINER_EMAIL'],
                    'tags': csplit(row['TAGS']),
                    'extras': {
                        'date_released': row['RELEASE_DATE'],
                        'categories': csplit(row['CATEGORIES']),
                        'geographical_granularity': row['GEOGRAPHY'],
                        'geographical_coverage': row['EXTENT'],
                        'temporal_granularity': row['UPDATE_FREQUENCY'],
                        'temporal_coverage': row['DATE_RANGE'],
                        'license_summary': row['LICENSE_SUMMARY'],
                        'license_details': row['license_details'],
                        'spatial_reference_system': row['spatial_ref'],
                        'harvest_dataset_url': row['DATASTORE_URL'],
                        # Common extras
                        'harvest_catalogue_name': 'London Datastore',
                        'harvest_catalogue_url': 'http://data.london.gov.uk',
                        'eu_country':'UK',
                        'eu_nuts1':'UKI'

                    },
                    'resources': []
                }

            def pkg_format(prefix, mime_type):
                if row.get(prefix + "_URL"):
                    package_dict['resources'].append({
                        'url': row.get(prefix + "_URL"),
                        'format': mime_type,
                        'description': "%s version" % prefix.lower()
                        })

            pkg_format('EXCEL', 'application/vnd.ms-excel')
            pkg_format('CSV', 'text/csv')
            pkg_format('TAB', 'text/tsv')
            pkg_format('XML', 'text/xml')
            pkg_format('GOOGLEDOCS', 'api/vnd.google-spreadsheet')
            pkg_format('JSON', 'application/json')
            pkg_format('SHP', 'application/octet-stream+esri')
            pkg_format('KML', 'application/vnd.google-earth.kml+xml')
        except Exception, e:
            log.exception(e)
            self._save_object_error('%r' % e, harvest_object, 'Import')

        package_dict['id'] = harvest_object.guid
        package_dict['name'] = self._gen_new_name(package_dict['title'])
        tags = []
        for tag in package_dict.get('tags', []):
            tag = re.sub(r'[^a-zA-Z0-9 ]','',tag).replace(' ','-').lower()
            if not tag in tags:
                tags.append(tag)
        package_dict['tags'] = tags
        return self._create_or_update_package(package_dict, harvest_object)


from lxml import html, etree
from hashlib import sha1
class DataWienGvAtHarvester(PDEUHarvester):
    CATALOGUE_FEED_URL = "http://data.wien.gv.at/katalog/.indexR.xml"

    def info(self):
        return {
            'name': 'data_wien_gv_at',
            'title': 'Open Government Data Wien',
            'description': 'CSV Import from GLA Datastore'
        }

    def gather_stage(self, harvest_job):
        log.debug('In DataWienGvAt gather_stage')
        
        doc = etree.parse(self.CATALOGUE_FEED_URL)
        ids = []
        for link in doc.findall("//item/link"):
            link = link.text
            id = sha1(link).hexdigest()
            obj = HarvestObject(guid=id, job=harvest_job, content=link)
            obj.save()
            ids.append(obj.id)
        return ids

    def fetch_stage(self, harvest_object):
        doc = html.parse(harvest_object.content)
        package_dict = {'extras': {'harvest_dataset_url': harvest_object.content}, 
                        'resources': []}
        package_dict['title'] = doc.findtext('//title').split(' | ')[0]
        if not doc.find('//table[@class="BDE-table-frame vie-ogd-table"]'):
            return False
        for meta in doc.findall("//meta"):
            key = meta.get('name')
            value = meta.get('content')
            if key is None or value is None:
                continue
            if key == 'DC.Creator':
                package_dict['author'] = value
            elif key == 'DC.date.created':
                package_dict['metadata_created'] = value
            elif key == 'DC.date.modified':
                package_dict['metadata_modified'] = value
            elif key == 'keywords':
                package_dict['tags'] = []
                for tag in value.split(','):
                    tag = tag.strip()
                    tag = re.sub(r'[^a-zA-Z0-9 ]','',tag).replace(' ','-').lower()
                    if not tag in package_dict['tags']:
                        package_dict['tags'].append(tag)
        for row in doc.findall('//table[@class="BDE-table-frame vie-ogd-table"]//tr'):
            key = row.find('th/p').text
            elem = row.find('td')
            if key == 'Beschreibung': 
                package_dict['notes'] = elem.xpath("string()")
            elif key == 'Bezugsebene':
                package_dict['extras']['geographic_coverage'] = elem.xpath("string()")
            elif key == 'Zeitraum': 
                package_dict['extras']['temporal_coverage'] = elem.xpath("string()")
            elif key == 'Aktualisierung': 
                package_dict['extras']['temporal_granularity'] = elem.xpath("string()")
            elif key == 'Kategorien': 
                package_dict['extras']['categories'] = elem.xpath("string()")
            elif key == 'Typ': 
                package_dict['extras']['type'] = elem.xpath("string()")
            elif key == u'Attribute': 
                elem.tag = 'span'
                package_dict['extras']['attributes'] = etree.tostring(elem)
            elif key == u'Datenqualität': 
                package_dict['extras']['data_quality'] = elem.xpath("string()")
            elif key == 'Kontakt': 
                package_dict['maintainer'] = elem.xpath("string()")
            elif key == 'Lizenz':
                if 'by/3.0/at/deed.de' in elem.findall('.//a')[0].get('href'):
                    package_dict['license_id'] = 'cc-by'
            elif key == 'Datensatz':
                for li in elem.findall('.//li'):
                    link = li.find('.//a').get('href')
                    if li.find('.//abbr') is not None:
                        res = {'description': li.xpath('string()'),
                               'url': link, 
                               'format': li.find('.//abbr').text}
                        package_dict['resources'].append(res)
                    else:
                        package_dict['url'] = link

        harvest_object.content = json.dumps(package_dict)
        harvest_object.save()
        return True

    def import_stage(self,harvest_object):
        if not harvest_object:
            log.error('No harvest object received')
            return False

        if harvest_object.content is None:
            self._save_object_error('Empty content for object %s' % harvest_object.id,harvest_object,'Import')
            return False

        try:
            package_dict = json.loads(harvest_object.content)
            package_dict['id'] = harvest_object.guid
            package_dict['name'] = self._gen_new_name(package_dict['title'])

            # Common extras
            package_dict['extras']['harvest_catalogue_name'] = u'Open Government Data Wien'
            package_dict['extras']['harvest_catalogue_url'] = u'http://data.wien.gv.at'
            package_dict['extras']['eu_country'] = u'AT'
            package_dict['extras']['eu_nuts2'] = u'AT13'

            return self._create_or_update_package(package_dict, harvest_object)
        except Exception, e:
            log.exception(e)
            self._save_object_error('%r' % e, harvest_object, 'Import')


class OpendataParisFrHarvester(PDEUHarvester):
    PREFIX_URL = "http://opendata.paris.fr/opendata/"
    CATALOGUE_INDEX_URL = "jsp/site/Portal.jsp?page_id=5"

    def info(self):
        return {
            'name': 'opendata_paris_fr',
            'title': 'Paris Open Data',
            'description': 'Bienvenue sur ParisData, le site de la politique Open Data de la Ville de Paris.'
        }

    def gather_stage(self, harvest_job):
        log.debug('In OpendataParisFr gather_stage')
        
        doc = html.parse(self.PREFIX_URL + self.CATALOGUE_INDEX_URL)
        ids = []
        for link in doc.findall("//div[@class='animate download-portlet-element']/a"):
            link = link.get('href')
            if not "#comments" in link:
                id = sha1(link).hexdigest()
                obj = HarvestObject(guid=id, job=harvest_job, content=link)
                obj.save()
                ids.append(obj.id)
        return ids

    def fetch_stage(self, harvest_object):
        doc = html.parse(self.PREFIX_URL + harvest_object.content)
        package_dict = {'extras': {}, 'resources': [], 'tags': []}
        package_dict['title'] = doc.findtext('//h3[@class="fullpage-header"]')
        package_dict['author'] = doc.find('//meta[@name="author"]').get('content')
        package_dict['extras']['harvest_dataset_url'] = self.PREFIX_URL + harvest_object.content
        for p in doc.findall('//div[@id="content"]//p'):
            section = p.find('strong')
            if section is None:
                continue
            key = section.text.strip().encode('utf-8')
            value = section.tail.strip().encode('utf-8')
            if 'Mots' in key:
                for tag in p.findtext('.//span[@id="tags"]').split(','):
                    tag = tag.strip()
                    tag = re.sub(r'[^a-zA-Z0-9 ]','',tag).replace(' ','-').lower()
                    if not tag in package_dict['tags']:
                        package_dict['tags'].append(tag)
            elif 'Description' in key:
                package_dict['notes'] = value
            elif 'publication' in key:
                package_dict['metadata_created'] = value
            elif 'riode couverte par le jeu de don' in key: 
                package_dict['extras']['temporal_coverage'] = value
            elif 'quence de mise' in key: 
                package_dict['extras']['temporal_granularity'] = value
            elif 'Th' in key: 
                package_dict['extras']['categories'] = value
        
        res = self.PREFIX_URL + doc.find('//a[@id="f1"]').get('href')
        package_dict['resources'].append({
            'url': res, 
            'format': '', 
            'description': 'Telecharger'
            })
        package_dict['license_id'] = 'odc-odbl'
        harvest_object.content = json.dumps(package_dict)
        harvest_object.save()
        return True

    def import_stage(self,harvest_object):
        if not harvest_object:
            log.error('No harvest object received')
            return False

        if harvest_object.content is None:
            self._save_object_error('Empty content for object %s' % harvest_object.id,harvest_object,'Import')
            return False

        try:
            package_dict = json.loads(harvest_object.content)
            package_dict['id'] = harvest_object.guid
            package_dict['name'] = self._gen_new_name(package_dict['title'])

            # Common extras
            package_dict['extras']['harvest_catalogue_name'] = u'ParisData'
            package_dict['extras']['harvest_catalogue_url'] = u'http://opendata.paris.fr'
            package_dict['extras']['eu_country'] = u'FR'
            package_dict['extras']['eu_nuts3'] = u'FR101'

            return self._create_or_update_package(package_dict, harvest_object)
        except Exception, e:
            log.exception(e)
            self._save_object_error('%r' % e, harvest_object, 'Import')





