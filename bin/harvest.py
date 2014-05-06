"""
Author: Matthew Russell
Date: 3 May 2014

Summary:

This script is a work in progress and part of a larger decoupled application architecture
that creates a personalized aggregated news feed as part of a project for Hack Nashville 5.
Much of the original code is drawn from Mining the Social Web, 2nd Edition (O'Reilly, 2013)
at https://github.com/ptwobrussell/Mining-the-Social-Web-2nd-Edition

This script basically harvests links from a Twitter user's home timeline, manufactures rich 
metadata about the links (in a fairly concurrent fashion using gevents), and populates a 
MongoDB database collection with objects that represent each news item. The metadata for each
news item is fairly rich and contains things like the title of the target web page,
a short abstract of the content, the resolved domain name (which may not be apparent
from the shortlink itself), the screen name of the person who tweeted about the story, etc.

Additional downstream services enrich the data in MongoDB with ranking/filtering that is
predicated on machine learning to ultimately deliver a personalized news feed as part of
a fuller news application.
"""

import sys
import time
import nltk
import numpy
import requests
#from contextlib import closing #XXX Need this to wrap (g)requests.get???
from boilerpipe.extract import Extractor
import twitter
import io
import json
import time
from httplib import BadStatusLine
from BeautifulSoup import BeautifulSoup
import urllib
import os
import grequests
import urllib2
import logging
import codecs
from urlparse import urlparse
from collections import Counter
import uuid
from optparse import OptionParser
import pymongo

MY_NAME = sys.argv[0].split(".")[0]

# A little session keeping/logging happens along the way
SESSION_ID = unicode(uuid.uuid4().get_hex())
TEMP_DIR_BASE = "/tmp/{0}-tmp/".format(MY_NAME)
TEMP_DIR = os.path.join(TEMP_DIR_BASE, "session-{0}")

# take the first three arguments and write them to the file that oauth_login will expect
# XXX: For now, UID Is referenced in oauth_login to simplify invocations
UID, oauth_token, oauth_secret = sys.argv[1], sys.argv[2], sys.argv[3]
my_twitter_creds_file = os.path.expanduser(os.path.join(TEMP_DIR_BASE, UID + '.twitter.oauth'))
twitter.write_token_file(my_twitter_creds_file, oauth_token, oauth_secret)

# Create a Twitter app and populate these credentials with the values
# See https://apps.twitter.com/

# XXX: Move these values to an external config file to prevent accidental checkin
CONSUMER_KEY = ''
CONSUMER_SECRET = ''

# Name this something unique
MONGODB_DATABASE_NAME="hacknashville"
MONGODB_COLL_NAME="harvests"

# For piping Unicode through stdout
sys.stdout=codecs.getwriter('utf-8')(sys.stdout)

logging.basicConfig(filename=os.path.join(TEMP_DIR_BASE, MY_NAME + '.log'),
                    level=logging.INFO,
                    format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s')

try:
    os.mkdir(TEMP_DIR_BASE)
except:
    pass

def log_timing(func_to_decorate):
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func_to_decorate(*args, **kwargs)
        elapsed = (time.time() - start)
        
        logging.info("[TIMING]:%s - %s -%s" % (func_to_decorate.__name__, str(args), elapsed))
        
        return result
    wrapper.__doc__ = func_to_decorate.__doc__
    wrapper.__name__ = func_to_decorate.__name__
    return wrapper

# Assumes that twitter.write_token_file has already been called to store uid.twitter.oauth
# so that this invocation cal access the oauth token/secret
def oauth_login():
    # See https://dev.twitter.com/docs/auth/oauth for more information 
    # on Twitter's OAuth implementation.

    my_twitter_creds = os.path.expanduser(os.path.join(TEMP_DIR_BASE, UID + '.twitter.oauth'))

    # No need to do this...
    #if not os.path.exists(my_twitter_creds):
    #    twitter.oauth_dance("nuztap", CONSUMER_KEY, CONSUMER_SECRET,
    #                my_twitter_creds)

    oauth_token, oauth_secret = twitter.read_token_file(my_twitter_creds)

    auth=twitter.OAuth(oauth_token, oauth_secret, CONSUMER_KEY, CONSUMER_SECRET)

    twitter_api = twitter.Twitter(auth=auth)

    return twitter_api

def save_json(filename, data):

    try:
        os.mkdir(TEMP_DIR.format(SESSION_ID))
    except:
        pass

    with io.open(os.path.join(TEMP_DIR.format(SESSION_ID), '{0}.json'.format(filename)), 
                 'w', encoding='utf-8') as f:
        f.write(unicode(json.dumps(data, ensure_ascii=False)))

def load_json(filename, session_id=None):
    with io.open(os.path.join(TEMP_DIR.format(session_id), '{0}.json'.format(filename)), 
                 encoding='utf-8') as f:
        return json.loads(f.read())


def summarize(url=None, html=None, n=100, cluster_threshold=5, top_sentences=5):

    # Adapted from "The Automatic Creation of Literature Abstracts" by H.P. Luhn
    #
    # Parameters:
    # * n  - Number of words to consider
    # * cluster_threshold - Distance between words to consider
    # * top_sentences - Number of sentences to return for a "top n" summary
            
    # Begin - nested helper function
    def score_sentences(sentences, important_words):
        scores = []
        sentence_idx = -1
    
        for s in [nltk.tokenize.word_tokenize(s) for s in sentences]:
    
            sentence_idx += 1
            word_idx = []
    
            # For each word in the word list...
            for w in important_words:
                try:
                    # Compute an index for important words in each sentence
    
                    word_idx.append(s.index(w))
                except ValueError, e: # w not in this particular sentence
                    pass
    
            word_idx.sort()
    
            # It is possible that some sentences may not contain any important words
            if len(word_idx)== 0: continue
    
            # Using the word index, compute clusters with a max distance threshold
            # for any two consecutive words
    
            clusters = []
            cluster = [word_idx[0]]
            i = 1
            while i < len(word_idx):
                if word_idx[i] - word_idx[i - 1] < cluster_threshold:
                    cluster.append(word_idx[i])
                else:
                    clusters.append(cluster[:])
                    cluster = [word_idx[i]]
                i += 1
            clusters.append(cluster)
    
            # Score each cluster. The max score for any given cluster is the score 
            # for the sentence.
    
            max_cluster_score = 0
            for c in clusters:
                significant_words_in_cluster = len(c)
                total_words_in_cluster = c[-1] - c[0] + 1
                score = 1.0 * significant_words_in_cluster \
                    * significant_words_in_cluster / total_words_in_cluster
    
                if score > max_cluster_score:
                    max_cluster_score = score
    
            scores.append((sentence_idx, score))
    
        return scores    
    
    # End - nested helper function
    
    extractor = Extractor(extractor='ArticleExtractor', url=url, html=html)

    # It's entirely possible that this "clean page" will be a big mess. YMMV.
    # The good news is that the summarize algorithm inherently accounts for handling
    # a lot of this noise.

    txt = extractor.getText()
    
    sentences = [s for s in nltk.tokenize.sent_tokenize(txt)]
    normalized_sentences = [s.lower() for s in sentences]

    words = [w.lower() for sentence in normalized_sentences for w in
             nltk.tokenize.word_tokenize(sentence)]

    fdist = nltk.FreqDist(words)

    top_n_words = [w[0] for w in fdist.items() 
            if w[0] not in nltk.corpus.stopwords.words('english')][:n]

    scored_sentences = score_sentences(normalized_sentences, top_n_words)

    # Summarization Approach 1:
    # Filter out nonsignificant sentences by using the average score plus a
    # fraction of the std dev as a filter

    avg = numpy.mean([s[1] for s in scored_sentences])
    std = numpy.std([s[1] for s in scored_sentences])
    mean_scored = [(sent_idx, score) for (sent_idx, score) in scored_sentences
                   if score > avg + 0.5 * std]

    # Summarization Approach 2:
    # Another approach would be to return only the top N ranked sentences

    top_n_scored = sorted(scored_sentences, key=lambda s: s[1])[-top_sentences:]
    top_n_scored = sorted(top_n_scored, key=lambda s: s[0])

    # Decorate the post object with summaries

    return dict(top_n_summary=[sentences[idx] for (idx, score) in top_n_scored],
                mean_scored_summary=[sentences[idx] for (idx, score) in mean_scored])


def make_twitter_request(twitter_api_func, max_errors=10, *args, **kw): 
    
    # A nested helper function that handles common HTTPErrors. Return an updated
    # value for wait_period if the problem is a 500 level error. Block until the
    # rate limit is reset if it's a rate limiting issue (429 error). Returns None
    # for 401 and 404 errors, which requires special handling by the caller.
    def handle_twitter_http_error(e, wait_period=2, sleep_when_rate_limited=True):
    
        if wait_period > 3600: # Seconds
            logging.error('Too many retries. Quitting.')
            raise e
    
        # See https://dev.twitter.com/docs/error-codes-responses for common codes
    
        if e.e.code == 401:
            logging.error('Encountered 401 Error (Not Authorized)')
            return None
        elif e.e.code == 404:
            logging.error('Encountered 404 Error (Not Found)')
            return None
        elif e.e.code == 429: 
            logging.error('Encountered 429 Error (Rate Limit Exceeded)')
            if sleep_when_rate_limited:
                logging.error("Retrying in 15 minutes...ZzZ...")
                sys.stderr.flush()
                time.sleep(60*15 + 5)
                logging.error('...ZzZ...Awake now and trying again.')
                return 2
            else:
                raise e # Caller must handle the rate limiting issue
        elif e.e.code in (500, 502, 503, 504):
            logging.error('Encountered %i Error. Retrying in %i seconds' % (e.e.code, wait_period))
            time.sleep(wait_period)
            wait_period *= 1.5
            return wait_period
        else:
            raise e

    # End of nested helper function
    
    wait_period = 2 
    error_count = 0 

    while True:
        try:
            return twitter_api_func(*args, **kw)
        except twitter.api.TwitterHTTPError, e:
            error_count = 0 
            wait_period = handle_twitter_http_error(e, wait_period)
            if wait_period is None:
                return
        except urllib2.URLError, e:
            error_count += 1
            logging.error("URLError encountered. Continuing.")
            if error_count > max_errors:
                logging.error("Too many consecutive errors...bailing out.")
                raise
        except BadStatusLine, e:
            error_count += 1
            logging.error("BadStatusLine encountered. Continuing.")
            if error_count > max_errors:
                logging.error("Too many consecutive errors...bailing out.")
                raise

def harvest_user_timeline(twitter_api, screen_name=None, user_id=None, max_results=1000):
     
    assert (screen_name != None) != (user_id != None), \
    "Must have screen_name or user_id, but not both"    
    
    kw = {  # Keyword args for the Twitter API call
        'count': 200,
        'trim_user': 'false',
        'include_rts' : 'true',
        'since_id' : 1
        }
    
    if screen_name:
        kw['screen_name'] = screen_name
    else:
        kw['user_id'] = user_id
        
    max_pages = 16
    results = []
    
    tweets = make_twitter_request(twitter_api.statuses.user_timeline, **kw)
    
    if tweets is None: # 401 (Not Authorized) - Need to bail out on loop entry
        tweets = []
        
    results += tweets
    
    logging.info('Fetched %i tweets' % len(tweets))
    
    page_num = 1
    
    # Many Twitter accounts have fewer than 200 tweets so you don't want to enter
    # the loop and waste a precious request if max_results = 200.
    
    # Note: Analogous optimizations could be applied inside the loop to try and 
    # save requests. e.g. Don't make a third request if you have 287 tweets out of 
    # a possible 400 tweets after your second request. Twitter does do some 
    # post-filtering on censored and deleted tweets out of batches of 'count', though,
    # so you can't strictly check for the number of results being 200. You might get
    # back 198, for example, and still have many more tweets to go. If you have the
    # total number of tweets for an account (by GET /users/lookup/), then you could 
    # simply use this value as a guide.
    
    if max_results == kw['count']:
        page_num = max_pages # Prevent loop entry
    
    while page_num < max_pages and len(tweets) > 0 and len(results) < max_results:
    
        # Necessary for traversing the timeline in Twitter's v1.1 API:
        # get the next query's max-id parameter to pass in.
        # See https://dev.twitter.com/docs/working-with-timelines.
        kw['max_id'] = min([ tweet['id'] for tweet in tweets]) - 1 
    
        tweets = make_twitter_request(twitter_api.statuses.user_timeline, **kw)
        results += tweets

        logging.info('Fetched %i tweets' % (len(tweets),))
    
        page_num += 1
        
    logging.error('Done fetching tweets')

    return results[:max_results]


def harvest_home_timeline(twitter_api, max_results=800, since_id=1):
     
    kw = {  # Keyword args for the Twitter API call
        'count': 200,
        'include_entities' : 1,
        'trim_user': 0,
        'since_id' : 1
        }
    
    max_pages = 4 # Max of 800 results
    results = []
    
    tweets = make_twitter_request(twitter_api.statuses.home_timeline, **kw)
    
    results += tweets
    
    logging.error('Fetched %i tweets' % len(tweets))
    
    page_num = 1
    
    if max_results <= kw['count']:
        page_num = max_pages # Prevent loop entry
    
    while page_num < max_pages and len(tweets) > 0:
    
        # Necessary for traversing the timeline in Twitter's v1.1 API:
        # get the next query's max-id parameter to pass in.
        # See https://dev.twitter.com/docs/working-with-timelines.
        kw['max_id'] = min([ tweet['id'] for tweet in tweets]) - 1 
    
        tweets = make_twitter_request(twitter_api.statuses.user_timeline, **kw)
        results += tweets

        logging.error('Fetched %i tweets' % (len(tweets),))
    
        page_num += 1
        
    logging.error('Done fetching tweets')

    return results
 

def extract_tweet_entities(statuses):
    
    # See https://dev.twitter.com/docs/tweet-entities for more details on tweet
    # entities

    if len(statuses) == 0:
        return [], [], [], [], []
    
    screen_names = [ (status['id_str'], user_mention['screen_name'])
                         for status in statuses
                            for user_mention in status['entities']['user_mentions'] ]
    
    hashtags = [ (status['id_str'], hashtag['text'])
                     for status in statuses 
                        for hashtag in status['entities']['hashtags'] ]

    urls = [ (status['id_str'], url['expanded_url']) 
                     for status in statuses 
                        for url in status['entities']['urls'] ]
    
    symbols = [ (status['id_str'], symbol['text'])
                   for status in statuses
                       for symbol in status['entities']['symbols'] ]
               
    # In some circumstances (such as search results), the media entity
    # may not appear
    if status['entities'].has_key('media'): 
        media = [ (status['id_str'], media['url'])
                         for status in statuses  
                            for media in status['entities'].get('media', '') ]
    else:
        media = []

    return screen_names, hashtags, urls, media, symbols    

# This hook is called for each redirect in the response chain, not just the "final" response.
# XXX: This hook needs to handle exceptions in the greenlets once grequests #22 is merged - https://github.com/kennethreitz/grequests/pull/22
def process_response_hook(context, response_hook_results):

    def hook(res, **kw):
        start = time.time()

        # Pick up this value from the closure
        res.context = context

        if res.status_code == 200:
            msg = u"\t".join([unicode(res.status_code), res.url, res.context['original_url'], unicode(res.context['tweet_id']), res.url, unicode(time.time() - start)])
            logging.info(msg)
        elif res.status_code in (301,302, 303): # Redirects
            msg = u"\t".join([unicode(res.status_code), res.url, res.context['original_url'], unicode(res.context['tweet_id']), res.url, unicode(time.time() - start)])
            logging.info(msg)
            return
        elif res.status_code == 404: # Not found
            msg = u"\t".join([unicode(res.status_code), res.url, res.context['original_url'], unicode(res.context['tweet_id']), res.url, unicode(time.time() - start)])
            logging.warn(msg)
            return
        else: #Probably a 40X level issue that will require some thought
            msg = u"\t".join([unicode(res.status_code), res.url, res.context['original_url'], unicode(res.context['tweet_id']), res.url, unicode(time.time() - start)])
            logging.error(msg)
            return

        # Strict checking on headers for now. Perhaps even too strict since some web pages may not declare it at all.
        # MIME types we definitely would want to avoid: ("image", "application", "audio", "message", "model", "multipart", "video"):
        if not res.headers.get("content-type", "").lower().split("/")[0].startswith("text"):
            logging.warn(u"Skipping content because of header content-type: {0} doesn't start with 'text'".format(res.headers.get("content-type")))
        else:

            try:
                if res.context['content_size'] is None: # Get everything. 
                    content = res.text # Ask for the response as Unicode
                else: 
                    # Get some portion of the content over the stream
                    try:
                        # XXX: Might be more proper to ask for smaller chunks? Or would a requests & the server automatically deal with this if too large?
                        #      Some common pages like Amazon.com URLs may be ~500KB with the <title> being  ~50KB+ into the page stream
                        content = res.iter_content(chunk_size=res.context['content_size'], decode_unicode=True).next()
                    except StopIteration:
                        content = u''
            except Exception, e:
                print "Exception during", res.url
                raise e

            try:
                soup = BeautifulSoup(content)
                title = u' '.join(soup.title.text.replace("\n", " ").strip().split())
            except:
                title = u'???'

            # Only return a tuple of this info if we've successfully processed. Otherwise, the response
            # is returned, which may be chained across redirects, etc.
            msg = u"\t".join([unicode(res.status_code), res.url, res.context['original_url'], unicode(res.context['tweet_id']), res.url, title, unicode(time.time() - start)])
            logging.info(msg)
            response_hook_results.append( (res.context['tweet_id'], dict(original_url=res.context['original_url'], 
                                               tweet_id=unicode(res.context['tweet_id']), 
                                               final_url=res.url, 
                                               web_page_title=title, 
                                               content=content,
                                               proc_time_secs=unicode(time.time() - start)),) )


        # End of scope for hook()
    
    # process_response_hook() creates a closure and returns hook
    return hook

def get_user_timeline_tweets(screen_name=None, num_tweets=50):
    logging.info("Getting Tweets From User Timeline")

    twitter_api = oauth_login()

    if screen_name is None:
        # Use authenticating user's screen_name
        screen_name = twitter_api.account.verify_credentials()['screen_name']

    tweets = harvest_user_timeline(twitter_api, screen_name=screen_name, max_results=num_tweets)

    return tweets

def get_home_timeline_tweets(num_tweets=50):
    logging.info("Getting Tweets")

    twitter_api = oauth_login()
    tweets = harvest_home_timeline(twitter_api, max_results=num_tweets)

    return tweets

def get_web_page_urls(tweets=None):
    logging.info("Extracting Tweet Entities")

    screen_names, hashtags, urls, media, symbols = extract_tweet_entities(tweets.values())

    return urls

def get_web_page_meta(urls, mutable_results_reference, content_size=4096):
    logging.info("Fetching Titles of Web Pages")

    # This callback references mutable_results_reference
    
    rs = (grequests.get(url, timeout=15,  # XXX: Should be more like 5 seconds
                             callback=process_response_hook(dict(original_url=url, tweet_id=tweet_id, content_size=content_size), 
                             mutable_results_reference)) 
            for (tweet_id, url) in urls)
    grequests.map(rs, stream=True)

    return mutable_results_reference

def get_authenticated_screen_name():
    twitter_api = oauth_login()
    return twitter_api.account.verify_credentials()['screen_name']

def build_features(tweets, web_page_meta):

    # A trivial surrogate for a ML predicted rank from a model, built from your tweet history
    def _rank(t):
       return 1.5*t['favorite_count'] + t['retweet_count']

    features = []

    authenticated_twitter_screen_name=get_authenticated_screen_name(),

    for tweet_id, wp in web_page_meta.items():

        f = dict(title=wp['web_page_title'],
                 url=wp['final_url'],
                 retweet_count=tweets[tweet_id]['retweet_count'],
                 favorite_count=tweets[tweet_id]['favorite_count'],
                 source=tweets[tweet_id]['user']['screen_name'],
                 retweeted=tweets[tweet_id]['retweeted'],
                 favorited=tweets[tweet_id]['favorited'],
                 tweet_text=tweets[tweet_id]['text'],
                 rank=_rank(tweets[tweet_id]),
                 tweet_id=tweet_id,
                 tweet_created_at=tweets[tweet_id]['created_at'],

                 # can adjust with _{size}.extension per https://dev.twitter.com/docs/user-profile-images-and-banners
                 source_image_url=tweets[tweet_id]['user']['profile_image_url'],
                 source_type='twitter', # XXX: For now, just twitter


                # XXX: Implement extraction of a real image from the story if available.

                 story_image='http://jquery.bassistance.de/contenteditable/demo/img/placeholder.jpg',

                 # XXX: Potentially lots of other useful features to pull in here from the tweet,
                 #      especially tweet entities.

                 twitter_screen_name=authenticated_twitter_screen_name,

                 hostname=urlparse(wp['final_url']).hostname,

                 uid=UID,
                )

        try:
            summary=u' '.join(summarize(html=wp['content'])['top_n_summary'])
        except Exception, e:
            logging.error("Could not generate summary for {0}".format(wp['final_url']))
            summary = u'Error generating summary'
        
        f['summary'] = summary

        features.append(f)

    return features

def print_features_tsv(features):

    columns = features[0].keys()

    print(u'\t'.join([ unicode(column) for column in columns ]))

    for feature in features:
        print(u'\t'.join([ unicode(feature[column]) for column in columns ]))

# Download the jQuery tablesorter bundle from http://tablesorter.com/docs/#Download
# and serve with python -m SimpleHTTPServer
def print_features_html(features):

    columns = features[0].keys()

    rows = []
    
    rows.append(u'<thead>\n\t<tr>' + u''.join([ u'<th>{0}</th>'.format(unicode(column)) for column in columns ]) + u'</tr></thead>\n')

    rows.append(u'<tbody>\n')
    for feature in features:
        rows.append(u'\t<tr>\n' + u''.join([ u'\t\t<td>{0}</td>\n'.format(unicode(feature[column])) for column in columns ]) + u'</tr>\n')
    rows.append(u'</tbody>\n')

    table = u'<table id="myTable" class="tablesorter">\n{0}\n</table>'.format(u''.join(rows))

    # Note the use of {{ and }} below. Yuck. Could use string.Template instead if the code gets any more complex,
    # or use %s instead of format
    html = u"""<html>
<head>
    <meta charset="utf-8">

    <script type="text/javascript" src="tablesorter/jquery-latest.js"></script>
    <script type="text/javascript" src="tablesorter/jquery.tablesorter.js"></script>
    <link rel="stylesheet" href="tablesorter/themes/blue/style.css" type="text/css" media="print, projection, screen" />
    <script type="text/javascript">
        $(document).ready(function() {{
            $("#myTable").tablesorter();
        }});
    </script>
</head>
<body>
{0}
</body>
</html>""".format(table)

    print(html)


# Similar to print_features_html except that it tries to provide a better ux versus showing you everything as a matrix
def print_html(features):

    def _title(f):
        return u'<a href="{0}" target="_blank">{1}</a> ({2})'.format(f['url'], f['title'], f['hostname'])

    def _source(f):
        return u'<a href="http://twitter.com/{0}" target="_blank">@{1}</a>'.format(f['source'], f['source'])

    def _tweet_text(f):
        return u'{2}<br />(<a href="http://twitter.com/{0}/status/{1}" target="_blank">view tweet</a>)'.format(f['source'], f['tweet_id'], f['tweet_text'])
    
    columns = ("Title", "Excerpt", "Source", "Tweet", "Rank", )

    rows = []
    
    rows.append(u'<thead>\n\t<tr>' + u''.join([ u'<th>{0}</th>'.format(unicode(column)) for column in columns ]) + u'</tr></thead>\n')

    rows.append(u'<tbody>\n')
    for feature in features:

        title = _title(feature)
        summary = feature['summary']
        source = _source(feature)
        tweet_text = _tweet_text(feature)
        rank = feature['rank']

        row = [title, summary, source, tweet_text, rank]

        rows.append(u'\t<tr>\n' + u''.join([ u'\t\t<td>{0}</td>\n'.format(unicode(item)) for item in row ]) + u'</tr>\n')

    rows.append(u'</tbody>\n')

    table = u'<table id="myTable" class="tablesorter">\n{0}\n</table>'.format(u''.join(rows))

    # Note the use of {{ and }} below. Yuck. Could use string.Template instead if the code gets any more complex,
    # or use %s instead of format
    html = u"""<html>
<head>
    <meta charset="utf-8">

    <script type="text/javascript" src="tablesorter/jquery-latest.js"></script>
    <script type="text/javascript" src="tablesorter/jquery.tablesorter.js"></script>
    <link rel="stylesheet" href="tablesorter/themes/blue/style.css" type="text/css" media="print, projection, screen" />
    <script type="text/javascript">
        $(document).ready(function() {{
            //$("#myTable").tablesorter();
            $("#myTable").tablesorter( {{sortList: [[4,1]]}} ); 
        }});
    </script>
</head>
<body>
{0}
</body>
</html>""".format(table)

    print(html)

def print_features(features, indent=4):
    print json.dumps(features, indent=indent)
   

def insert_features_into_mongo(features):
    coll_name = MONGODB_COLL_NAME
    return save_to_mongo(features, MONGODB_DATABASE_NAME, coll_name)
 
def save_to_mongo(data, mongo_db, mongo_db_coll, **mongo_conn_kw):
    
    # Connects to the MongoDB server running on 
    # localhost:27017 by default
    
    client = pymongo.MongoClient(**mongo_conn_kw)
    
    # Get a reference to a particular database
    
    db = client[mongo_db]
    
    # Reference a particular collection in the database
    
    coll = db[mongo_db_coll]
    
    # Perform a bulk insert and  return the IDs
    
    return coll.insert(data)


if __name__ == '__main__':


    # A few options to facilitate debugging/dev workflows. Using --session_id is especially useful during development
    parser = OptionParser()
    
    parser.add_option("--user_timeline", dest="user_timeline", default=False,
            help="A screen name that specifies a user timeline to retrieve. If unspecified, the authenticating user's user timeline is used")

    parser.add_option("--home_timeline", dest="home_timeline", default=False, action="store_true",
            help="If specified, use the authenticating user's home timeline. The --user_timeline option is ignored if this option is specified")

    parser.add_option("--num_tweets", dest="num_tweets", default=50,
            help="The number of tweets to fetch from the timeline")

    parser.add_option("--session_id", dest="session_id", default=None,
            help="An existing session id value that provides data to use instead of fetching new data. Useful for downstream development/debugging")

    options, _ = parser.parse_args()


    #################
    # Process options
    #################

    # If there is an existing session id, don't fetch new data
    if options.session_id is None:

        logging.info("Starting work for new session {0}".format(SESSION_ID))

        # Keep track of the options used for this session
        save_json('options', unicode(options))

        if (not options.home_timeline and not options.user_timeline) or options.home_timeline:
            tweets = get_home_timeline_tweets(num_tweets=options.num_tweets)
        else:
            tweets = get_user_timeline_tweets(screen_name=(options.user_timeline or None), num_tweets=options.num_tweets)

        tweets = dict([(unicode(t['id']), t) for t in tweets])
        save_json('tweets', tweets)

        # A mutable reference to pass in for collecting async results. Returns tuples
        # that can be cast into a dict, keyed off of tweet_id
        web_page_meta = [] 

        # XXX: Could make these be totally streaming now that data is being written to Mongo.
        urls = get_web_page_urls(tweets)
        get_web_page_meta(urls, web_page_meta, content_size=None)
        web_page_meta = dict(web_page_meta)

        save_json('web_page_meta', web_page_meta) 

        # Build features that can be used to identify the news
        # XXX: Currently missing the full text and summary of the page in web_page_meta

    else:

        logging.info("Loading data from existing session {0}".format(options.session_id))

        tweets = load_json('tweets', session_id=options.session_id)
        web_page_meta = load_json('web_page_meta', session_id=options.session_id)


    features = build_features(tweets, web_page_meta)

    print >> sys.stderr
    print >> sys.stderr, "*"*25, (options.session_id or SESSION_ID), "*"*25
    print >> sys.stderr

    #print_html(features)
    #print_features(features)

    # Insert new data into a particular collection that another service manages. MongoDB acts as a 
    # sort of queue or data broker in this regard and is the "glue" that ties the app together.
    insert_features_into_mongo(features) 
    print >> sys.stderr
