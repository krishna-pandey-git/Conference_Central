#!/usr/bin/env python
#Fi
"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21
updated by krishna on 2016 jan 08

"""

__author__ = 'wesc+api@google.com (Wesley Chun) and Krishna Pandey'


from datetime import datetime
import copy

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb
from google.appengine.api import memcache
from google.appengine.api import taskqueue

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import StringMessage
from models import Session
from models import SessionForm
from models import SessionForms
from models import SessionQueryForm
from models import SessionQueryForms
from models import WishListRequestForm
from models import WishList
from models import FeaturedSpeakerMessage

from utils import getUserId

from settings import WEB_CLIENT_ID

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
MEMCACHE_FEATURED_SPEAKER_KEY = "FEATURED_SPEAKER"

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

SESSIONFIELDS =    {
            'SPEAKER': 'speaker',
            'DURATION': 'duration',
            'TYPEOFSESSION': 'typeOfSession',
            'STARTTIME': 'startTime',
            }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey = messages.StringField(1),
)

SESSION_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey = messages.StringField(1),
)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

@endpoints.api(name='conference', version='v1', 
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        # TODO 2: add confirmation email sending task to queue
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )

        return request


    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)


    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id =  getUserId(user)
        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )


    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"].upper()]
                filtr["operator"] = OPERATORS[filtr["operator"].upper()]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                conferences]
        )


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        #if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        #else:
                        #    setattr(prof, field, val)
            prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
         for conf in conferences]
        )


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = '%s %s' % (
                'Last chance to attend! The following conferences '
                'are nearly sold out:',
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement

    @staticmethod
    def _featuredSpeaker(speaker, websafeConferenceKey):
        """finds out featuredspeaker & assign to memcache"""
        conf = ndb.Key(urlsafe=websafeConferenceKey).get()
        conf_key = conf.key

        sessions = Session.query(ancestor=conf_key)
        sessions = sessions.filter(Session.speaker == speaker)
        if sessions.count()>1:
            sessions_name = ', '.join([session.name for session in sessions])
            announcement =  "our featured speaker is %s and he is speaking at %s" % (speaker, sessions_name)
        else:
            announcement = ""
        memcache.set(MEMCACHE_FEATURED_SPEAKER_KEY, announcement)



    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        # TODO 1
        # return an existing announcement from Memcache or an empty string.
        announcement = memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY)
        if not announcement:
            announcement = ""
        return StringMessage(data=announcement)

    @endpoints.method(message_types.VoidMessage, FeaturedSpeakerMessage,
            path='conference/announcement/featuredspeakder/get',
            http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return Announcement from memcache."""
        # TODO 1
        # return an existing announcement from Memcache or an empty string.
        announcement = memcache.get(MEMCACHE_FEATURED_SPEAKER_KEY)
        if not announcement:
            announcement = ""
        return FeaturedSpeakerMessage(data=announcement)

# - - - Sessions - - - - - - - - - - - - - - - - - - - -

    def _copySessionToForm(self, session):
        """ This method copies values of a session object to SessionForm messages """
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(session, field.name):
                if field.name =="startTime" or field.name == "date":
                    setattr(sf,field.name,str(getattr(session, field.name)))
                else :
                    setattr(sf, field.name, getattr(session, field.name))    
            elif field.name == 'websafeSessionKey':
                setattr(sf, field.name, session.key.urlsafe())            
        #sf.check_initialized()
        return sf

    def _getConferenceSessions(self, request, typeOfSession=None, speaker=None):
        """Its a multi purpose method which will return session forms for below three combination
         - websafeConferenceKey
         - websafeConferenceKey with typeOfSession
         - speaker  """
        wsck = request.websafeConferenceKey

        # If type of session provided without conference key then its an error
        if typeOfSession and not wsck:
            raise endpoints.BadRequestException("If typeOfSession given then confernce key should also be provided.")

        if wsck:
            # if conf key availabe then get all its child sessions
            conf = ndb.Key(urlsafe=wsck).get()
            conf_key = conf.key
            if not conf:
                raise endpoints.NotFoundException('conference is invalid')

            sessions = Session.query(ancestor=conf_key)
            # filter type of session if provided
            if typeOfSession is not None:
                sessions = sessions.filter(Session.typeOfSession == typeOfSession)
        else: # if conf key is none then filter by speaker only
            sessions = Session.query(Session.speaker == speaker)
        return SessionForms(
            sessions = [self._copySessionToForm(session)for session in sessions])


    def _createSession(self,request):
        """CreateSession - to create session when provided with websafeConferenceKey
        along with other property, Name property is required """
        prof = self._getProfileFromUser()
        user_id = prof.mainEmail

        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        conf_key = conf.key
        if not conf:
            raise endpoints.NotFoundException('Conference key is invalid')

        if user_id != conf.organizerUserId:
            raise endpoints.UnauthorizedException(
                'you are not the %s'% conf.organizerUserId)

        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        if not data['name'] or data['name'].strip()=="":
            raise endpoints.BadRequestException("you must provide name for the session.")
        if data['startTime']:
            data['startTime']=datetime.strptime("1970-01-01 "+data['startTime'][:8],"%Y-%m-%d %H:%M")
        if data['date']:
            data['date'] = datetime.strptime(data['date'][:10],"%Y-%m-%d").date()
        
        del data['websafeSessionKey']
        del data['websafeConferenceKey']

        s_id = Session.allocate_ids(size=1, parent=conf_key )[0]
        s_key = ndb.Key(Session, s_id, parent = conf_key)
        data['key']=s_key
        
        Session(**data).put()
        session = s_key.get()

        taskqueue.add(params={'speaker': data['speaker'],
            'websafeConferenceKey': wsck},
            url='/tasks/setFeaturedSpeaker'
        )

        return session


    @endpoints.method(SESSION_GET_REQUEST, SessionForms,
            path = 'conference/session/{websafeConferenceKey}',
            http_method='GET', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Return all sessions of a given Conference """
        #prof = self._getProfileFromUser()        
        if not request.websafeConferenceKey:
            raise endpoints.NotFoundException('Web conference key is not provided')
        return self._getConferenceSessions(request)

    @endpoints.method(SessionQueryForm, SessionForms,
            path = 'conference/session/type',
            http_method='GET', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self,request):
        """ Return all sessions from a conference with particular type """
        return self._getConferenceSessions(request, typeOfSession = request.typeOfSession)

    @endpoints.method(SessionQueryForm, SessionForms,
            path = 'conference/session/byspeaker',
            http_method='GET', name='getConferenceSessionsBySpeaker')
    def getConferenceSessionsBySpeaker(self,request):
        """ Return all sessions from a conference with particular type """
        return self._getConferenceSessions(request, speaker=request.speaker)

    @endpoints.method(SESSION_POST_REQUEST, SessionForm,
            path = 'conference/session/create/{websafeConferenceKey}',
            http_method='POST', name='createSession')
    def createSession(self, request):
        """Used to create session for a particular Conference,
        Only organiser can create session for a Conference """
        session = self._createSession(request)
        return self._copySessionToForm(session)

       

# - - - WishList - - - - - - - - - - - - - - - - - - - - - - - -

    def _handleWishList(self, request, rem=False):
        """ add and delete sessions from wish list in datastore"""
        prof = self._getProfileFromUser()
        mainEmail = prof.mainEmail

        wishes = WishList.query()
        wishes = wishes.filter(WishList.mainEmail == mainEmail)
        wish = wishes.get()
        
        # check if its a request for removing the session from wishlist
        if rem:
            for s_key in request.sessionKeys:
                if ndb.Key(urlsafe=s_key) not in wish.sessionKeys:
                    # request to remove sessions that does not exist
                    raise ConflictException("session %s not present in your wish list"% s_key)
                else :                    
                    wish.sessionKeys.remove(ndb.Key(urlsafe=s_key))
        else :            
            if not wish: # request to add a new seesion for user adding firsttime in wishlist
                wish = WishList(
                    mainEmail= mainEmail,
                    sessionKeys= [ndb.Key(urlsafe=s_key) for s_key in request.sessionKeys],)
            else : # reqeust to add a new session for user already have a wishlist
                for s_key in request.sessionKeys:
                    if ndb.Key(urlsafe=s_key) in wish.sessionKeys :
                        raise ConflictException("%s Session is already added in your WishList."% s_key)
                    else:
                        wish.sessionKeys.append(ndb.Key(urlsafe=s_key))        
        wish.put()

        sessions = [s_key.get() for s_key in wish.sessionKeys]

        return SessionForms(
            sessions = [self._copySessionToForm(session) for session in sessions]) 
        

    @endpoints.method(WishListRequestForm, SessionForms,
            path = 'conference/session/wishlist/add',
            http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """ Used to add session in users WishList"""
        return self._handleWishList(request)


    @endpoints.method(WishListRequestForm, message_types.VoidMessage,
            path = 'conference/session/wishlist/delete',
            http_method='POST', name='deleteSessionInWishlist')
    def deleteSessionInWishlist(self, request):
        """ Used to add session in users WishList"""
        self._handleWishList(request, True)
        return message_types.VoidMessage()


    @endpoints.method(message_types.VoidMessage, SessionForms,
            path = 'conference/session/wishlist/get',
            http_method='POST', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """ Used to get all sessions in users WishList"""
        prof = self._getProfileFromUser()
        mainEmail = prof.mainEmail

        wishes = WishList.query()
        wishes = wishes.filter(WishList.mainEmail == mainEmail)
        wish = wishes.get()

        if wish:
            sessions = [s_key.get() for s_key in wish.sessionKeys]
        else:
            sessions = []

        return SessionForms(
            sessions = [self._copySessionToForm(session) for session in sessions])

# - - - One Additional Endponts to search Session with various field combination - - - - - 

    def _getQuerySession(self, request):
        """Return formatted query from the submitted filters."""
        q = Session.query()
        inequality_filter, filters = self._formatFiltersSession(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Session.startTime)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Session.startTime)

        for filtr in filters:
            if filtr["field"] in ["duration"]:
                filtr["value"] = int(filtr["value"])
            elif filtr['field'] == "startTime":
                filtr["value"] = datetime.strptime("1970-01-01 "+filtr["value"],"%Y-%m-%d %H:%M")
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFiltersSession(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = SESSIONFIELDS[filtr["field"].upper()]
                filtr["operator"] = OPERATORS[filtr["operator"].upper()]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(SessionQueryForms, SessionForms,
            path = 'conference/Session/get/query',
            http_method='POST', name='getSessionsWithFilters')
    def getSessionsWithFilters(self, request):
        """ Used to get all sessions with various filters and their
        combination """
        sessions = self._getQuerySession(request)

        return SessionForms(
            sessions = [self._copySessionToForm(session) for session in sessions])

    @endpoints.method(SessionQueryForms, SessionForms,
            path = 'conference/Session/get/twoInequality',
            http_method='POST', name='getSessionsTwoInequality')
    def getSessionsTwoInequality(self, request):
        """ Used to get all sessions in case two different field
            with inequality operator it will also work with equality operator"""
        
        try :
            secondFilter = request.filters[1]
            del request.filters[1]
        except:
            raise endpoints.BadRequestException("You should provide two fitering condition")

        sessions = self._getQuerySession(request)

        sessionsforms = SessionForms(
            sessions = [self._copySessionToForm(session) for session in sessions])

        secondQuerySessions = copy.deepcopy(sessionsforms)
        #--- Below code is to handle second inequality condition on different property ----
        for s in sessionsforms.sessions:
            if getattr(secondFilter, "operator") == "GT":
                if getattr(s ,getattr(secondFilter,"field")) <= getattr(secondFilter, "value"):
                    secondQuerySessions.sessions.remove(s)
            elif getattr(secondFilter, "operator") == "LT":
                if getattr(s ,getattr(secondFilter,"field")) >= getattr(secondFilter, "value"):
                    secondQuerySessions.sessions.remove(s)
            elif getattr(secondFilter, "operator") == "LTEQ":
                if getattr(s ,getattr(secondFilter,"field")) > getattr(secondFilter, "value"):
                    secondQuerySessions.sessions.remove(s)
            elif getattr(secondFilter, "operator") == "GTEQ":
                if getattr(s ,getattr(secondFilter,"field")) < getattr(secondFilter, "value"):
                    secondQuerySessions.sessions.remove(s)
            elif getattr(secondFilter, "operator") == "NE":
                if getattr(s ,getattr(secondFilter,"field")) == getattr(secondFilter, "value"):
                    secondQuerySessions.sessions.remove(s)
            elif getattr(secondFilter, "operator") == "EQ":
                if getattr(s ,getattr(secondFilter,"field")) != getattr(secondFilter, "value"):
                    secondQuerySessions.sessions.remove(s)
                 
        return secondQuerySessions

    
api = endpoints.api_server([ConferenceApi]) # register API
