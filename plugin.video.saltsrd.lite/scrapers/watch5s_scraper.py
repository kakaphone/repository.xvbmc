"""
    SALTS XBMC Addon
    Copyright (C) 2014 tknorris

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
import re
import urlparse
import urllib
from salts_lib import dom_parser
from salts_lib import kodi
from salts_lib import log_utils
from salts_lib import scraper_utils
from salts_lib.constants import FORCE_NO_MATCH
from salts_lib.constants import QUALITIES
from salts_lib.constants import VIDEO_TYPES
import scraper
import xml.etree.ElementTree as ET


BASE_URL = 'http://watch5s.com/'
LINK_URL = '/player/'
Q_MAP = {'TS': QUALITIES.LOW, 'CAM': QUALITIES.LOW, 'HDTS': QUALITIES.LOW, 'HD-720P': QUALITIES.HD720}
XHR = {'X-Requested-With': 'XMLHttpRequest'}

class Watch5s_Scraper(scraper.Scraper):
    base_url = BASE_URL

    def __init__(self, timeout=scraper.DEFAULT_TIMEOUT):
        self.timeout = timeout
        self.base_url = kodi.get_setting('%s-base_url' % (self.get_name()))

    @classmethod
    def provides(cls):
        return frozenset([VIDEO_TYPES.MOVIE, VIDEO_TYPES.SEASON, VIDEO_TYPES.EPISODE])

    @classmethod
    def get_name(cls):
        return 'Watch5s'

    def resolve_link(self, link):
        return link

    def format_source_label(self, item):
        label = '[%s] %s' % (item['quality'], item['host'])
        return label

    def get_sources(self, video):
        source_url = self.get_url(video)
        hosters = []
        sources = {}
        if source_url and source_url != FORCE_NO_MATCH:
            page_url = urlparse.urljoin(self.base_url, source_url)
            html = self._http_get(page_url, cache_limit=1)
            match = re.search("filmInfo\.filmIMAGE\s*=\s*'([^']+)", html, re.I)
            film_image = match.group(1) if match else ''
            match = re.search("filmInfo\.filmID\s*=\s*'([^']+)", html, re.I)
            film_id = match.group(1) if match else ''
            if film_image and film_id:
                for item in dom_parser.parse_dom(html, 'div', {'class': 'les-content'}):
                    button_labels = dom_parser.parse_dom(item, 'a', {'class': '[^"]*btn-eps[^"]*'})
                    servers = dom_parser.parse_dom(item, 'a', ret='episode-sv')
                    ep_ids = dom_parser.parse_dom(item, 'a', ret='episode-id')
                    referers = dom_parser.parse_dom(item, 'a', ret='href')
                    for label, ep_sv, ep_id, referer in zip(button_labels, servers, ep_ids, referers):
                        if video.video_type == VIDEO_TYPES.EPISODE:
                            try: ep_num = int(label)
                            except: ep_num = 0
                            if ep_num != int(video.episode):
                                continue
                            
                        headers = XHR
                        headers['Referer'] = urlparse.urljoin(self.base_url, referer)
                        link_url = urlparse.urljoin(self.base_url, LINK_URL)
                        data = {'epSV': ep_sv, 'epID': ep_id, 'filmID': film_id, 'filmIMAGE': film_image}
                        html = self._http_get(link_url, data=data, headers=headers, cache_limit=.5)
                        iframe_url = dom_parser.parse_dom(html, 'iframe', ret='src')
                        if iframe_url:
                            quality = Q_MAP.get(label, QUALITIES.HIGH)
                            sources.update({iframe_url[0]: {'quality': quality, 'direct': False}})
                        else:
                            match = re.search('var\s+url_playlist\s*=\s*"([^"]+)', html)
                            if match:
                                sources.update(self.__get_links_from_xml(match.group(1), headers, label))
        
        for source in sources:
            if not source.lower().startswith('http'): continue
            if sources[source]['direct']:
                host = self._get_direct_hostname(source)
                if host != 'gvideo':
                    stream_url = source + '|User-Agent=%s&Referer=%s' % (scraper_utils.get_ua(), page_url)
                else:
                    stream_url = source
            else:
                host = urlparse.urlparse(source).hostname
                stream_url = source
            hoster = {'multi-part': False, 'host': host, 'class': self, 'quality': sources[source]['quality'], 'views': None, 'rating': None, 'url': stream_url, 'direct': sources[source]['direct']}
            hosters.append(hoster)
        return hosters

    def __get_links_from_xml(self, xml_url, headers, button_label):
        sources = {}
        try:
            xml = self._http_get(xml_url, headers=headers, cache_limit=.25)
            root = ET.fromstring(xml)
            for item in root.findall('.//item'):
                for source in item.findall('{http://rss.jwpcdn.com/}source'):
                    stream_url = source.get('file')
                    label = source.get('label')
                    if self._get_direct_hostname(stream_url) == 'gvideo':
                        quality = scraper_utils.gv_get_quality(stream_url)
                    elif label:
                        quality = scraper_utils.height_get_quality(label)
                    else:
                        quality = Q_MAP.get(button_label, QUALITIES.HIGH)
                    sources[stream_url] = {'quality': quality, 'direct': True}
                    log_utils.log('Adding stream: %s Quality: %s' % (stream_url, quality), log_utils.LOGDEBUG)
        except Exception as e:
            log_utils.log('Exception during Watch5s XML Parse: %s' % (e), log_utils.LOGWARNING)

        return sources
    
    def get_url(self, video):
        return self._default_get_url(video)

    def _get_episode_url(self, season_url, video):
        url = urlparse.urljoin(self.base_url, season_url)
        html = self._http_get(url, cache_limit=8)
        for label in dom_parser.parse_dom(html, 'a', {'class': '[^"]*btn-eps[^"]*'}):
            try: ep_num = int(label)
            except: ep_num = 0
            if int(video.episode) == ep_num:
                return season_url
    
    def search(self, video_type, title, year, season=''):
        search_url = urlparse.urljoin(self.base_url, '/search/?q=')
        search_url += urllib.quote_plus(title)
        html = self._http_get(search_url, cache_limit=8)
        results = []
        for item in dom_parser.parse_dom(html, 'div', {'class': 'ml-item'}):
            match_title = dom_parser.parse_dom(item, 'span', {'class': 'mli-info'})
            match_url = re.search('href="([^"]+)', item, re.DOTALL)
            year_frag = dom_parser.parse_dom(item, 'img', ret='alt')
            is_episodes = dom_parser.parse_dom(item, 'span', {'class': 'mli-eps'})
            
            if (video_type == VIDEO_TYPES.MOVIE and not is_episodes) or (video_type == VIDEO_TYPES.SEASON and is_episodes):
                if match_title and match_url:
                    match_url = match_url.group(1)
                    match_title = match_title[0]
                    match_title = re.sub('</?h2>', '', match_title)
                    match_title = re.sub('\s+\d{4}$', '', match_title)
                    if video_type == VIDEO_TYPES.SEASON:
                        if season and not re.search('Season\s+%s$' % (season), match_title): continue
                        
                    if not match_url.endswith('/'): match_url += '/'
                    match_url = urlparse.urljoin(match_url, 'watch/')
                    match_year = ''
                    if video_type == VIDEO_TYPES.MOVIE and year_frag:
                        match = re.search('\s*-\s*(\d{4})$', year_frag[0])
                        if match:
                            match_year = match.group(1)
    
                    if not year or not match_year or year == match_year:
                        result = {'title': scraper_utils.cleanse_title(match_title), 'year': match_year, 'url': scraper_utils.pathify_url(match_url)}
                        results.append(result)

        return results