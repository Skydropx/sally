import os
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

    # TODO OJO Search related
    # search?q=educacion&type=page&fields=about,category,location,contact_address,emails,phone,engagement
    def __init__(self, source_file, spreadsheet, fb_user_id, *args, **kwargs):
        self.spreadsheetId = spreadsheet
        self.config = gs.get_settings()
        self.score = gs.get_score()
        self.collection = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        self.fb_user_id = fb_user_id
        self.access_token = self.get_token()
        self.graph = 'https://graph.facebook.com'
        self.sheet_rows = [
                ['SCORE','WEB SITE', 'ABOUT', 'CATEGORY', 'LIKES', 'TELPHONE',
                    'EMAIL', 'ADDRESS','CITY', 'COUNTRY', 'CRAWL DATE']
                ]

        lines = ["%s" % str(l).rstrip() for l in gs.get_urls(source_file)]
        fb = re.compile(r'facebook', re.IGNORECASE)
        self.start_urls = list(filter(fb.search, list(filter(None, ','.join(lines).split(',')))))
        logger.debug(self.start_urls)

        for url in self.start_urls:
            response = self.parse_item(url.split('/')[1])
            if 'error' in response:
                logger.debug(response['error']['message'])
            else:
                if 'location' in response:
                    city = response['location']['city'] if 'city' in response['location'] else None
                    street = response['location']['street'] if 'street' in response['location'] else ''
                    zip_code = response['location']['zip'] if 'zip' in response['location'] else ''
                    address = street +', '+zip_code
                    country = response['location']['country'] if 'country' in response['location'] else None
                else:
                    city = None
                    address = None
                    country = None
                score = self.qualify(response)

                self.persist(response)
                row = [
                        score,
                        response['website'] if 'website' in response else "https://www.facebook.com/%s" % url.split('/')[1],
                        response['about'] if 'about' in response else None,
                        response['category'] if 'category' in response else None,
                        response['engagement']['count'] if 'engagement' in response else None,
                        response['phone'] if 'phone' in response else None,
                        ','.join(response['emails']) if 'emails' in response else None,
                        address,
                        city,
                        country,
                        datetime.datetime.now().strftime("%m%d%Y")
                        ]
                logger.debug(row)
                self.sheet_rows.append(row)
            time.sleep(3)

        if len(self.sheet_rows) > 1:
            spreadsheet = gs.create_spreadsheet("fb%s" % self.collection)
            sheet = gs.create_sheet(spreadsheet['spreadsheetId'], self.collection)
            results = gs.insert_to(spreadsheet['spreadsheetId'], self.collection,
                    self.sheet_rows)
            logger.debug(results)


    def mongo_connect(self):
        connect(os.environ.get('MONGO_DBNAME'),
                host="mongodb://" + os.environ.get('MONGO_HOST'),
                port=int(os.environ.get('MONGO_PORT')),
                replicaset=os.environ.get('MONGO_REPLICA_SET'),
                username=os.environ.get('MONGO_USER'),
                password=os.environ.get('MONGO_PASSWORD'))


    def qualify(self, item):
        score = 1
        if ('emails' not in item or not item['emails']):
            score += self.score['email']
        if ('phone' not in item or not item['phone']):
            score += self.score['telephone']
        if ('engagement' not in item or item['engagement']['count'] < 1000):
            score += self.score['likes']

        return score


    def get_token(self):

        self.mongo_connect()
        try:
            user = model.User.objects(fb_userId=self.fb_user_id).get()
            return user.fb_accessToken
        except Exception as ex:
            logger.error(__name__, exc_info=True)
            return None


    def persist(self, item):
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


    def parse_item(self, page):
        """Extract data from facebook pages

        id    Page ID. No access token is required to access this field
        best_page    The best available Page on Facebook for the concept represented by this Page. The best available Page takes into account authenticity and the number of likes
        category    The Page's category. e.g. Product/Service, Computers/Technology
        category_list    The Page's sub-categories
        description    The description of the Page
        has_whatsapp_number    has whatsapp number
        hometown    Hometown of the band. Applicable to Bands
        name    The name of the Page
        phone    Phone number provided by a Page
        products    The products of this company. Applicable to Companies
        rating_count    Number of ratings for the page.
        website    The URL of the Page's website
        whatsapp_number    whatsapp number
        overall_star_rating    Overall page rating based on rating survey from users on a scale of 1-5. This value is normalized and is not guaranteed to be a strict average of user ratings.
        likes    The pages that this page liked
        link    The Page's Facebook URL
        connected_instagram_account    Instagram account connected to page via page settings
        emails    Update the emails field
        contact_address    The mailing or contact address for this page. This field will be blank if the contact address is the same as the physical address
        product_catalogs    Product catalogs owned by this page
        """

#        fields = str('?fields=about,category,contact_address,engagement,'
#        'emails,location,phone&access_token=')
        fields = str('?fields=about,category,contact_address,engagement,emails,'
                'location,phone,website,category_list,description,'
                'has_whatsapp_number,whatsapp_number,hometown,name,products,'
                'rating_count,overall_star_rating,link,'
                'connected_instagram_account&access_token=')
        logger.debug("%s/%s%s%s" % (self.graph, page, fields,
            self.access_token))
        r = requests.get("%s/%s%s%s" % (self.graph, page, fields,
            self.access_token))
        #logger.debug(r.text)
        return r.json()
