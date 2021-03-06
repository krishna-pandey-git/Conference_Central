App Engine application for the Udacity training course.

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
1. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the
   [Developer Console][4].
1. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
1. (Optional) Mark the configuration files as unchanged as follows:
   `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
1. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default [localhost:8080][5].)
1. (Optional) Generate your client library(ies) with [the endpoints tool][6].
1. Deploy your application.


[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool

##Speakers and Sessions Design Choice:

Speaker has been implemented as string property. Ideally speaker should have been implemented as different entity having its own
details like academic and their personal experience in the industry they work in. But I implemented it as string for the sake of simplicity.

Session has been implemented as a child of conference. Reason for this is logically speaking session belongs to a conference.
and also querying them become easy for sessions in a particular conference.
Session model has been provided with 7 property to hold different details associated with
a session.
	- name: Its a required property of the Session and its of type string.
	- highlights: Its a string property which will give us key info about the session
	- speaker: Its string property and has name of the person giving the session.
	- duration: Duration of the session. Its an integer property representing duration in minutes.
	- typeOfSession: Its a string property. Give us detail like what type of session e.g. WorkShop, Intro etc.
	- date: Its a datetime property. tells us the occurence date of the session.
	- startTime: Its a DateTimeProperty. Earlier opted for TimeProperty but it was not working properly.

##Session wishlist working
endpoint: addSessionToWishlist(self, request)

WishList: Its the model that has been created to store wishlist for a particular user.
It has two property.
	- sessionKeys: It holds multiple keys of the sessions that user has added in wishlist.
	  As it holds key of the Session this property is of KeyProperty type and it can hold
	  multiple keys.
	- mainEmail: Its the mainEmail id of the user. It helps to uniquely identify each user
	  wish list. Its a normal StringProperty.

Working: we need session's websafe url key to call addToWishlist endpoint. In order to get
websafeurl for the session we need to call any of the endpoints which returns sessions 
as response. From the response we can copy the websafeurl for the session.

In order to add sessions to your wish list you must know websafesessionkey before hand. you can add any session in your wishlist.
whether you already registered for that conference or not does not matter.
Multiple sessions can be added in one go.

similarly to delete sessions from your WishList you need to have the websafesessionkey first.

##Additional Queries

endpoint : getSessionsWithFilters
This is similar to what we have for conference where we can have multiple filtering option. I have created similar structure for sessions as well.

## Inequality filter on two different property

Issue : we can't apply inequality operator on two different property in a query. Its a datastore rule that we can not change.
Solution: First we will apply only the first inequality and then get the result in a list of sessions. Then we copy the whole list
	in a different list. Now while iterating over the previous list we will remove the object from the second list if it does not satisfy the 
	second inequality condition on different poperty.
	
endpoint name: getSessionsTwoInequality