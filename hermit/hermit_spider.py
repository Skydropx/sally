import os
import sys
import re
import datetime
import time
import logging
import requests
from mongoengine import connect
import hermit.model as model
import sally.google.spreadsheet as gs

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


class HermitCrab(object):
    """Facebook pages crawler"""

    def __init__(self, source_file, spreadsheet, fb_user_id, *args, **kwargs):
        self.spreadsheetId = spreadsheet
        self.config = gs.get_settings()
        self.score = gs.get_score()
        self.collection = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        self.fb_user_id = fb_user_id
        self.access_token = self.get_token()
        self.graph = 'https://graph.facebook.com'
        self.sheet_rows = [
                ['SCORE', 'WEB SITE', 'ABOUT', 'CATEGORY', 'LIKES', 'TELPHONE',
                    'EMAIL', 'ADDRESS', 'CITY', 'COUNTRY', 'CRAWL DATE']
                ]
        self.categories = []

        lines = ["%s" % str(l).rstrip() for l in gs.get_urls(source_file)]
        fb = re.compile(r'facebook', re.IGNORECASE)
        self.start_urls = list(filter(
            fb.search,
            list(filter(None, ','.join(lines).split(',')))))
        logger.debug(self.start_urls)

        for url in self.start_urls:
            response = self.parse_item(url.split('/')[1])
            if 'error' in response:
                logger.info(response['error']['message'])
            else:
                self.persist(response)
                item = self.process_response(response)
                row = self.build_row(item)
                self.sheet_rows.append(row)
            time.sleep(3)

        # Send to google spreadsheet
        self.insert_sheet(self.sheet_rows)

        # Go get pages alike
        if len(self.categories) > 1:
            for cat in list(set(self.categories)):
                rows = []
                for i in self.search_alike(cat)['data']:
                    self.persist(i)
                    rows.append(self.build_row(self.process_response(i)))
                    time.sleep(3)
                self.insert_sheet(rows)

        sys.exit(0)


    def insert_sheet(self, rows):
        """Create a Google spreadhseet and insert given rows to it."""
        if len(rows) > 1:
            spreadsheet = gs.create_spreadsheet("fb%s" % self.collection)
            sheet = gs.create_sheet(
                    spreadsheet['spreadsheetId'],
                    self.collection)
            results = gs.insert_to(
                    spreadsheet['spreadsheetId'],
                    self.collection,
                    self.sheet_rows)
            logger.debug(results)


    def mongo_connect(self):
        """Establish a MongoDB connection."""
        connect(os.environ.get('MONGO_DBNAME'),
                host="mongodb://" + os.environ.get('MONGO_HOST'),
                port=int(os.environ.get('MONGO_PORT')),
                replicaset=os.environ.get('MONGO_REPLICA_SET'),
                username=os.environ.get('MONGO_USER'),
                password=os.environ.get('MONGO_PASSWORD'))


    def qualify(self, item):
        """Return score for given item."""
        score = 1
        if ('emails' not in item or not item['emails']):
            score += self.score['email']
        if ('phone' not in item or not item['phone']):
            score += self.score['telephone']
        if ('engagement' not in item or item['engagement']['count'] < 1000):
            score += self.score['likes']

        return score


    def get_token(self):
        """Return Facebook user token from data base."""
        self.mongo_connect()
        try:
            user = model.User.objects(fb_userId=self.fb_user_id).get()
            return user.fb_accessToken
        except Exception as ex:
            logger.error(__name__, exc_info=True)
            return None


    def persist(self, item):
        """Persist item to database."""
        try:
            page = model.FbPage(
                title = item['name'] if 'name' in item else None,
                about = item['about'] if 'about' in item else None,
                category = item['category'] if 'category' in item else None,
                engagement = item['engagement'] if 'engagement' in item else None,
                emails = item['emails'] if 'emails' in item else None,
                location = item['location'] if 'location' in item else None,
                phone = item['phone'] if 'phone' in item else None,
                website = item['website'] if 'website' in item else None,
                category_list = item['category_list'] if 'category_list' in item else None,
                whatsapp_number = item['whatsapp_number'] if 'whatsapp_number' in item else None,
                link = item['link'] if 'link' in item else None,
                score_values = item['score_values'] if 'score_values' in item else None,
                score = item['score'] if 'score' in item else None,
                )
            return page.save()
        except Exception as ex:
            logger.error(ex, exc_info=True)
            return None


    def search_alike(self, category):
        """Return related pages by category."""
        query = "search?q=%s&limit=1000&metadata=1" % category
        fields = str('&fields=about,category,contact_address,engagement,emails,'
                'location,phone,website,category_list,description,'
                'has_whatsapp_number,whatsapp_number,hometown,name,products,'
                'rating_count,overall_star_rating,link,'
                'connected_instagram_account&access_token=')
        r = requests.get("%s/%s&type=page%s%s" % (self.graph, query, fields,
            self.access_token))
        return r.json()


    def process_response(self, response):
        """Return valid values for response items."""
        item = {}
        if 'website' in response:
            item['website'] = response['website']
        if 'about' in response:
            item['about'] = response['about']
        else:
            item['about'] = None
        if 'category' in response:
            item['category'] = response['category']
            self.categories.append(item['category'])
        else:
            item['category'] = None
        if 'engagement' in response:
            item['likes'] = response['engagement']['count']
        else:
            item['engagement'] = None
        if 'phone' in response:
            item['phone'] = response['phone']
        else:
            item['phone'] = None
        if 'emails' in response:
            item['emails'] = ','.join(response['emails'])
        else:
            item['emails'] = None
        if 'location' in response:
            item['city'] = response['location']['city'] if 'city' in response['location'] else None
            item['street'] = response['location']['street'] if 'street' in response['location'] else ''
            item['zip_code'] = response['location']['zip'] if 'zip' in response['location'] else ''
            item['address'] = item['street'] + ', ' + item['zip_code']
            item['country'] = response['location']['country'] if 'country' in response['location'] else None
        else:
            item['city'] = None
            item['address'] = None
            item['country'] = None
        item['score'] = self.qualify(response)
        return item


    def build_row(self, item):
        """Return a row for insert_to google spreadsheet"""
        return [
                item['score'],
                item['website'] if 'website' in item else '',
                item['about'],
                item['category'],
                item['likes'],
                item['phone'],
                item['emails'],
                item['address'],
                item['city'],
                item['country'],
                datetime.datetime.now().strftime("%m%d%Y")
                ]


    def parse_item(self, page):
        """Extract data from facebook pages"""
        fields = str('?fields=about,category,contact_address,engagement,emails,'
                'location,phone,website,category_list,description,'
                'has_whatsapp_number,whatsapp_number,hometown,name,products,'
                'rating_count,overall_star_rating,link,'
                'connected_instagram_account&access_token=')
        r = requests.get("%s/%s%s%s" % (self.graph, page, fields,
            self.access_token))
        return r.json()
