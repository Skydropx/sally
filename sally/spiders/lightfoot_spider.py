import os
from copy import deepcopy
from datetime import datetime
import re
from urllib.parse import urlparse
from itertools import filterfalse
import tldextract
import sendgrid
from sendgrid.helpers.mail import *
import scrapy
from scrapy.spiders import CrawlSpider, Rule
from scrapy.linkextractors import LinkExtractor
from scrapy.loader import ItemLoader
from sally.items import WebsiteItem
import sally.google.spreadsheet as gs
import sally.google.drive as gd


class BasicCrab(CrawlSpider):

    ELEMENTS = ['div', 'p', 'span', 'a', 'li']

    name = "lightfoot"

    # TODO I still don't knpw what to do with the rules
    rules = (Rule(LinkExtractor(unique=True), callback='parse_link'))

    def __init__(self, csvfile, spreadsheet,
            *args, **kwargs):

        self.source_urls = csvfile
        self.spreadsheetId = spreadsheet
        # Fetch settings from Google spreadsheet
        self.config = gs.get_settings()
        self.score = gs.get_score()

        # Compile regexes
        # allowed_reg list of allowed TDLs to crawl
        allowed_reg = [re.compile(r"\.%s" % domain, re.IGNORECASE) for domain
                in self.config['allowed_domains']]
        # disallowed_reg list of disallowed TLDs not to crawl
        disallowed_reg = [re.compile(r"\.%s" % domain, re.IGNORECASE) for domain
                in self.config['disallowed_domains']]

        ## TODO maybe unused if uninitialized
        #lines = []

        lines = ["http://%s" % str(l).rstrip() for l in gs.get_urls(csvfile)]

        allowed_url = []
        for r in allowed_reg:
            allowed_url += list(filter(r.search, lines))

        disallowed_url = []
        for r in disallowed_reg:
            disallowed_url += list(filter(r.search, list(set(allowed_url))))

        self.start_urls = list(set(allowed_url).difference(set(disallowed_url)))


    def extract_title(self, response):
        """extract_title from <title> tag

        Returns {str} title"""
        try:
            return response.css('title::text').extract_first().strip()
        except Exception:
            self.logger.error('Extract title %s' % Exception)
            return 'N/T'


    def extract_email(self, response, elements, email_set=set({})):
        """Extract email from elements listed in ELEMENTS

        Returns a set() of emails
        """
        if len(elements) > 0:
            myset = set(response.xpath('//' + elements.pop()).re(
                r'\"?([-a-zA-Z0-9.`?{}]+@\w+\.[^png|jpg|gif]\w+\.\w*)"?'))
            return self.extract_email(response, elements, myset)
        else:
            return email_set


    def to_tel(self, raw, code, tel_list=[]):
        """Take a list of split telephones and returns a lisf of
       formated telephones

       Code 10 3 numbers for LADA
       Code 12 2 number is country code next 3 LADA

       Returns a list of telephones
       """
        if len(raw) > 0:
            try:
                num = '-'.join(raw[:raw.index('')])
                if len(num.replace('-','')) == code:
                    tel_list.append(num)
                return self.to_tel(raw[raw.index(''):][1:], code, tel_list)
            except:
                return self.to_tel([], code, tel_list)
        else:
            return tel_list


    def extract_telephone(self, response, elements, tels=[]):
        """Extract telephone from elements listed in ELEMENTS

        Returns a set() of telephones
        """
        if len(elements) > 0:
            e = elements.pop()
            t334 = []
            t334 = response.xpath('//' + e).re(
                r'\(+(\d{3})\W*(\d{3})\W*(\d{4})\W*(\d*)\W*[^png|jpg|gif]')
            t224 = []
            t244 = response.xpath('//' + e).re(
                r'\(+(\d{2})\W*(\d{4})\W*(\d{4})\W*(\d*)\W*[^png|jpg|gif]')
            t_2_8 = response.xpath('//' + e).re(
                r'\(+(\d{2})\W*(\d{8})\W*[^png|jpg|gif]')
            t10 = []
            t10 = response.xpath('//' + e).re(
                r'\W*(\d{10})\W*[^png|jpg|gif]')
            tels.append('-'.join(t334[:3]))
            tels.append('-'.join(t334[3:3]))
            tels.append('-'.join(t334[6:3]))
            tels.append('-'.join(t244[:3]))
            tels.append('-'.join(t244[3:3]))
            tels.append('-'.join(t244[6:3]))
            # TODO append 10 digit numbers
            return self.extract_telephone(response, elements, list(filter(None,tels)))
        else:
            tset = set(tels)
            self.logger.debug(tset)
            if len(tset) > 0:
                return list(tset)
            else:
                return []


    def is_ecommerce(self, response):
        """Very simplistic e-commerce software detection

        Returns str of ecommerce software"""
        ecommerce = None
        full_text = response.xpath('//meta/@content').extract()
        if len(response.xpath('//script/@src').re(r'cdn\.shopify\.com')) > 0:
            # Look for cdn.shopify.com
            return 'shopify'
        if len(response.xpath('//meta[@name="generator"]/@content')
                .re(r'WooCommerce')) > 0:
            return 'woocommerce'
        elif len(response.xpath('//img/@src').re(r'cdn-shoperti\.global')) > 0:
            return 'shoperti'
        elif (len(response.xpath('//footer').re(r'[Mm]agento', re.IGNORECASE)) > 0
                or len(response.xpath('//head').re(r'[Mm]agento', re.IGNORECASE)) > 0):
            return 'magento'
        else:
            return 'N/E'


    def shoppingcart_detection(self, divs):
        result = []
        p = re.compile(r'cart')
        result += list(filter(p.search, divs))
        self.logger.debug(result)
        return list(set(result))


    def online_payment(self, links):
        elements = list(BasicCrab.ELEMENTS)
        result = []
        r = re.compile(r'paypal.me/\w*')
        result += list(filter(r.match, links))
        self.logger.debug(result)
        return result


    def extract_description(self, response):
        """extract_description from <meta name="description"> tags

        Returns list of descriptions"""
        return response.xpath('//meta[@name="description"]/@content').extract()


    def extract_keywords(self, response):
        """extract_keywords from <meta name="keywords" tag.

        Returns list of keywords"""
        return response.xpath('//meta[@name="keywords"]/@content').extract()


    def extract_social_networks(self, response, base_url,
            found=set({}), networks=[]):
        """extract_social_networks from <a href> tags, it matches agaist
        part of the base url.

        Returns set of social networks url found"""
        s = ''
        if type(base_url) is str:
            s = base_url
        else:
            if len(base_url) == 2:
                s = base_url[0]
            elif len(base_url) == 3:
                s = base_url[1]

        if len(networks) > 0:
            n = networks.pop()
            found.update(set(response.xpath('//a/@href').re(
                    r'(\w*\.' + n + '\/\w*' + s + '\w*)')))
            found.update(set(response.xpath('//a/@href').re(
                r'(\w*\.' + n + '\/\w*' + s[:3] + '\w*)')))
            return self.extract_social_networks(response, s, found,
                    networks)

        return found


    def extract_offer(self, website):
        # TODO make it better to store array of useful products in self['keywords']
        products = []
        if (type(website['keywords']) is list
            and len(website['keywords']) > 0 and website['keywords'][0] != ''):
            [products.append(p) for p
                    in website['keywords'][0].replace(' ','').split(',')
                    if p in self.config['allowed_keywords']]
        if (type(website['description']) is list
                and len(website['description']) > 0 and website['description'] != ''):
            [products.append(i) for i in website['description'][0].split(' ')
                    if i in self.config['allowed_keywords']]

        return products


    def clearset(self):
        s = set({})
        s.clear()
        return s


    def start_requests(self):
        """Returns iterable of Requests"""
        for url in self.start_urls:
            yield scrapy.Request(url=url, callback=self.parse_item)


    def parse_link(self, link):
        self.logger.info(link)


    def parse_item(self, response):
        # Collect all links found in crawled pages
        website_email = list(self.extract_email(response,
            list(BasicCrab.ELEMENTS)))
        tels = list()
        t334 = list()
        t334 = response.xpath('//' + BasicCrab.ELEMENTS[0]).re(
            r'\(+(\d{3})\W*(\d{3})\W*(\d{4})\W*(\d*)\W*[^png|jpg|gif]')
        tels.append('-'.join(t334[:3]))
        tels.append('-'.join(t334[3:3]))
        tels.append('-'.join(t334[6:3]))
        t224 = list()
        t244 = response.xpath('//' + BasicCrab.ELEMENTS[0]).re(
            r'\(+(\d{2})\W*(\d{4})\W*(\d{4})\W*(\d*)\W*[^png|jpg|gif]')
        tels.append('-'.join(t244[:3]))
        tels.append('-'.join(t244[3:3]))
        tels.append('-'.join(t244[6:3]))
        t_2_8 = list()
        t_2_8 = response.xpath('//' + BasicCrab.ELEMENTS[0]).re(
            r'\(+(\d{2})\W*(\d{8})\W*[^png|jpg|gif]')
        self.logger.debug(t_2_8)
        t334 = list()
        t334 = response.xpath('//' + BasicCrab.ELEMENTS[1]).re(
            r'\(+(\d{3})\W*(\d{3})\W*(\d{4})\W*(\d*)\W*[^png|jpg|gif]')
        tels.append('-'.join(t334[:3]))
        tels.append('-'.join(t334[3:3]))
        tels.append('-'.join(t334[6:3]))
        t224 = list()
        t244 = response.xpath('//' + BasicCrab.ELEMENTS[1]).re(
            r'\(+(\d{2})\W*(\d{4})\W*(\d{4})\W*(\d*)\W*[^png|jpg|gif]')
        tels.append('-'.join(t244[:3]))
        tels.append('-'.join(t244[3:3]))
        tels.append('-'.join(t244[6:3]))
        t334 = list()
        t334 = response.xpath('//' + BasicCrab.ELEMENTS[2]).re(
            r'\(+(\d{3})\W*(\d{3})\W*(\d{4})\W*(\d*)\W*[^png|jpg|gif]')
        tels.append('-'.join(t334[:3]))
        tels.append('-'.join(t334[3:3]))
        tels.append('-'.join(t334[6:3]))
        t224 = list()
        t244 = response.xpath('//' + BasicCrab.ELEMENTS[2]).re(
            r'\(+(\d{2})\W*(\d{4})\W*(\d{4})\W*(\d*)\W*[^png|jpg|gif]')
        tels.append('-'.join(t244[:3]))
        tels.append('-'.join(t244[3:3]))
        tels.append('-'.join(t244[6:3]))
        t334 = list()
        t334 = response.xpath('//' + BasicCrab.ELEMENTS[3]).re(
            r'\(+(\d{3})\W*(\d{3})\W*(\d{4})\W*(\d*)\W*[^png|jpg|gif]')
        tels.append('-'.join(t334[:3]))
        tels.append('-'.join(t334[3:3]))
        tels.append('-'.join(t334[6:3]))
        t224 = list()
        t244 = response.xpath('//' + BasicCrab.ELEMENTS[3]).re(
            r'\(+(\d{2})\W*(\d{4})\W*(\d{4})\W*(\d*)\W*[^png|jpg|gif]')
        tels.append('-'.join(t244[:3]))
        tels.append('-'.join(t244[3:3]))
        tels.append('-'.join(t244[6:3]))
        t334 = list()
        t334 = response.xpath('//' + BasicCrab.ELEMENTS[4]).re(
            r'\(+(\d{3})\W*(\d{3})\W*(\d{4})\W*(\d*)\W*[^png|jpg|gif]')
        tels.append('-'.join(t334[:3]))
        tels.append('-'.join(t334[3:3]))
        tels.append('-'.join(t334[6:3]))
        t224 = list()
        t244 = response.xpath('//' + BasicCrab.ELEMENTS[4]).re(
            r'\(+(\d{2})\W*(\d{4})\W*(\d{4})\W*(\d*)\W*[^png|jpg|gif]')
        tels.append('-'.join(t244[:3]))
        tels.append('-'.join(t244[3:3]))
        tels.append('-'.join(t244[6:3]))
        ## Social network detection TODO move it to function
        parsed_url = urlparse(response.url)
        website_network = list(self.extract_social_networks(response,
            parsed_url.netloc.split('.'), set({}),
            ['facebook\.com','instagram\.com','twitter\.com']))

        website = WebsiteItem()
        website.set_score(self.score)
        website['spreadsheetId'] = self.spreadsheetId
        website['base_url'] = parsed_url.netloc
        website['secure_url'] = True if parsed_url.scheme == 'https' else False
        website['url'] = response.url
        website['title'] = self.extract_title(response)
        website['link'] = [link for link in response.xpath('//a/@href').extract()]
        website['cart'] = self.shoppingcart_detection(
            response.xpath('//div/@class').extract() + response.xpath('//a/@class').extract() + response.xpath('//i/@class').extract())
        #self.online_payment(response.xpath('//div/@class').extract())
        website['network'] = website_network
        website['email'] = website_email
        website['telephone'] = list(set(tels))
        website['ecommerce'] = self.is_ecommerce(response)
        website['description'] = self.extract_description(response)
        website['keywords'] = self.extract_keywords(response)
        website['offer'] = self.extract_offer(website)
        website['last_crawl'] = datetime.now()

        return website


    def closed(self, reason):
        response = gd.mv(self.source_urls, os.environ.get('DRIVE_DONE'))
        # Send email with info about the results
        sg = sendgrid.SendGridAPIClient(apikey=os.environ.get('SENDGRID_API_KEY'))
        from_email = Email(os.environ.get('MAIL_FROM'))
        to_email = Email(os.environ.get('MAIL_TO'))
        subject = ("[lightfoot] terminó") #%s" % self.collection)
        content = Content("text/plain", "https://docs.google.com/spreadsheets/d/%s"
                % self.spreadsheetId)
        mail = Mail(from_email, subject, to_email, content)
        response = sg.client.mail.send.post(request_body=mail.get())


