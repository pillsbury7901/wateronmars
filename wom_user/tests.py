# -*- coding: utf-8; indent-tabs-mode: nil; python-indent: 2 -*-
#
# Copyright 2013 Thibauld Nion
#
# This file is part of WaterOnMars (https://github.com/tibonihoo/wateronmars) 
#
# WaterOnMars is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# WaterOnMars is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
# 
# You should have received a copy of the GNU Affero General Public License
# along with WaterOnMars.  If not, see <http://www.gnu.org/licenses/>.
#

from datetime import datetime
from datetime import timedelta
from django.utils import timezone
from django.utils import simplejson

from django.http import HttpResponse
from django.core.urlresolvers import reverse

from django.test import TestCase

from django.test.client import RequestFactory

from wom_pebbles.models import Reference
from wom_river.models import WebFeed

from wom_user.models import UserProfile
from wom_user.models import UserBookmark
from wom_user.models import ReferenceUserStatus


from wom_user.views import MAX_ITEMS_PER_PAGE
from wom_user.views import check_and_set_owner
from wom_user.views import loggedin_and_owner_required
from wom_user.tasks import import_user_feedsources_from_opml
from wom_user.tasks import import_user_bookmarks_from_ns_list
from wom_user.tasks import check_user_unread_feed_items

from wom_classification.models import Tag
from wom_classification.models import get_item_tag_names
from wom_classification.models import set_item_tag_names

from django.contrib.auth.models import User
from django.contrib.auth.models import AnonymousUser

class UserProfileModelTest(TestCase):

  def setUp(self):
    self.user = User.objects.create(username="name")
    
  def test_accessible_info(self):
    """
    Just to be sure what info we can access (not a "unit" test per
    se but useful anyway to make sure the model given enough
    information and list the info we rely on)
    """
    p = UserProfile.objects.create(owner=self.user)
    self.assertEqual(p.owner,self.user)
    self.assertEqual(0,len(p.sources.all()))
    self.assertEqual(0,len(p.web_feeds.all()))
    # just to be sure it is still provided by django
    self.assertNotEqual(p.owner.date_joined,None)


class UserBookmarkModelTest(TestCase):

  def setUp(self):
    self.date = datetime.now(timezone.utc)
    self.reference = Reference.objects.create(url="http://mouf",
                                              title="glop",
                                              pub_date=self.date)
    self.user = User.objects.create(username="name")
    
  def test_construction_defaults(self):
    """
    This tests just makes it possible to double check that a
    change in the default is voluntary.
    """
    b = UserBookmark.objects.create(owner=self.user,
                    reference=self.reference,
                    saved_date=self.date)
    self.assertFalse(b.is_public)

  def test_get_public_sources(self):
    source = Reference.objects.create(url="http://src",
                                      title="src",
                                      pub_date=self.date)
    b = UserBookmark.objects.create(owner=self.user,
                                    reference=self.reference,
                                    saved_date=self.date)
    b.reference.sources.add(source)
    userprofile = UserProfile.objects.create(owner=self.user)
    userprofile.sources.add(source)
    self.assertEqual([],list(b.get_public_sources()))
    userprofile.public_sources.add(source)
    self.assertEqual([source],list(b.get_public_sources()))
  
  def test_get_sources(self):
    source = Reference.objects.create(url="http://src",
                                      title="src",
                                      pub_date=self.date)
    b = UserBookmark.objects.create(owner=self.user,
                                    reference=self.reference,
                                    saved_date=self.date)
    b.reference.sources.add(source)
    userprofile = UserProfile.objects.create(owner=self.user)
    userprofile.sources.add(source)
    self.assertEqual([source],list(b.get_sources()))

  def test_set_public(self):
    source = Reference.objects.create(url="http://src",
                                      title="src",
                                      pub_date=self.date)
    b = UserBookmark.objects.create(owner=self.user,
                                    reference=self.reference,
                                    saved_date=self.date)
    b.reference.sources.add(source)
    userprofile = UserProfile.objects.create(owner=self.user)
    userprofile.sources.add(source)
    self.assertNotIn(source,userprofile.public_sources.all())
    b.set_public()
    self.assertIn(source,userprofile.public_sources.all())    
    self.assertIn(source,userprofile.sources.all())    

  def test_set_private_when_public(self):
    source = Reference.objects.create(url="http://src",
                                      title="src",
                                      pub_date=self.date)
    b = UserBookmark.objects.create(owner=self.user,
                                    reference=self.reference,
                                    saved_date=self.date)
    b.reference.sources.add(source)
    userprofile = UserProfile.objects.create(owner=self.user)
    userprofile.sources.add(source)
    userprofile.public_sources.add(source)
    b.is_public = True
    b.set_private()
    self.assertNotIn(source,userprofile.public_sources.all())
    self.assertIn(source,userprofile.sources.all())
    
  def test_set_private_when_has_feed(self):
    source = Reference.objects.create(url="http://src",
                                      title="src",
                                      pub_date=self.date)
    feed = WebFeed.objects.create(xmlURL="http://barf/bla.xml",
                                  last_update_check=self.date,
                                  source=source)
    b = UserBookmark.objects.create(owner=self.user,
                                    reference=self.reference,
                                    saved_date=self.date)
    b.reference.sources.add(source)
    userprofile = UserProfile.objects.create(owner=self.user)
    userprofile.web_feeds.add(feed)
    userprofile.sources.add(source)
    userprofile.public_sources.add(source)
    b.is_public = True
    b.set_private()
    # Since the feed exists: the source is still public !
    self.assertIn(source,userprofile.public_sources.all())
    self.assertIn(source,userprofile.sources.all())
    
class CheckAndSetOwnerDecoratorTest(TestCase):

  def setUp(self):
    self.user_a = User.objects.create(username="A")
    self.user_b = User.objects.create(username="B")
    self.request_factory = RequestFactory()
    def pass_through(request,owner_name):
      resp = HttpResponse()
      resp.request = request
      return resp
    self.pass_through_func = pass_through
    
  def test_call_with_user_owner(self):
    req = self.request_factory.get("/mouf")
    req.user = self.user_a
    res = check_and_set_owner(self.pass_through_func)(req,"A")
    self.assertEqual(200,res.status_code)
    self.assertTrue(hasattr(res.request,"owner_user"))
    self.assertEqual("A",res.request.owner_user.username)
    
  def test_call_with_non_owner_user(self):
    req = self.request_factory.get("/mouf")
    req.user = self.user_b
    res = check_and_set_owner(self.pass_through_func)(req,"A")
    self.assertEqual(200,res.status_code)
    self.assertTrue(hasattr(res.request,"owner_user"))
    self.assertEqual("A",res.request.owner_user.username)
    
  def test_call_for_invalid_owner(self):
    req = self.request_factory.get("/mouf")
    req.user = self.user_b
    res = check_and_set_owner(self.pass_through_func)(req,"C")
    self.assertEqual(404,res.status_code)


class LoggedInAndOwnerRequiredDecoratorTest(TestCase):

  def setUp(self):
    self.user_a = User.objects.create(username="A")
    self.user_b = User.objects.create(username="B")
    self.request_factory = RequestFactory()
    def pass_through(request,owner_name):
      resp = HttpResponse()
      resp.request = request
      return resp
    self.pass_through_func = pass_through
    
  def test_call_with_user_owner(self):
    req = self.request_factory.get("/mouf")
    req.user = self.user_a
    res = loggedin_and_owner_required(self.pass_through_func)(req,
                                  "A")
    self.assertEqual(200,res.status_code)
    self.assertTrue(hasattr(res.request,"owner_user"))
    self.assertEqual("A",res.request.owner_user.username)
    
  def test_call_with_non_owner_user(self):
    req = self.request_factory.get("/mouf")
    req.user = self.user_b
    res = loggedin_and_owner_required(self.pass_through_func)(req,"A")
    self.assertEqual(403,res.status_code)
    
  def test_call_for_invalid_owner(self):
    req = self.request_factory.get("/mouf")
    req.user = AnonymousUser()
    res = loggedin_and_owner_required(self.pass_through_func)(req,"A")
    self.assertEqual(302,res.status_code)
    
class UserProfileViewTest(TestCase):

  def setUp(self):
    self.user_a = User.objects.create_user(username="A",
                          password="pA")
    
  def test_get_html_user_profile(self):
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="A",password="pA"))
    resp = self.client.get(reverse("wom_user.views.user_profile"))
    self.assertEqual(200,resp.status_code)
    self.assertIn("profile.html",[t.name for t in resp.templates])
    self.assertIn("username", resp.context)
    self.assertEqual("A", resp.context["username"])
    self.assertIn("opml_form", resp.context)
    self.assertIn("nsbmk_form", resp.context)
    self.assertIn("collection_add_bookmarklet", resp.context)
    self.assertIn("source_add_bookmarklet", resp.context)

  def test_get_html_anonymous_profile(self):
    resp = self.client.get(reverse("wom_user.views.user_profile"))
    self.assertEqual(302,resp.status_code)

# # TODO: test ordering and paging !

class UserBookmarkAddTestMixin:
  """Mixin implementing the common tests for the Form and the REST API
  of bookmark addition.
  """

  def add_request(self,url,optionsDict):
    """
    Returns the response that can be received from the input url.
    """
    raise NotImplementedError("This method should be reimplemented")
  
  def setUp(self):
    date = datetime.now(timezone.utc)
    self.source = Reference.objects.create(
      url=u"http://mouf",
      title=u"mouf",
      pub_date=date)
    reference = Reference.objects.create(
      url=u"http://mouf/a",
      title=u"glop",
      pub_date=date)
    reference.sources.add(self.source)
    reference_private = Reference.objects.create(
      url=u"http://mouf/p",
      title=u"nop",
      pub_date=date)
    reference_private.sources.add(self.source)
    reference_b = Reference.objects.create(
      url=u"http://mouf/b",
      title=u"paglop",
      pub_date=date)
    reference_b.sources.add(self.source)
    self.user = User.objects.create_user(username="uA",
                                         password="pA")
    p = UserProfile.objects.create(owner=self.user)
    p.sources.add(self.source)
    self.bkm = UserBookmark.objects.create(
      owner=self.user,
      reference=reference,
      saved_date=date,
      is_public=True)
    self.bkm_private = UserBookmark.objects.create(
      owner=self.user,
      reference=reference_private,
      saved_date=date)
    self.other_user = User.objects.create_user(username="uB",
                                               password="pB")
    self.bkm_b = UserBookmark.objects.create(
      owner=self.other_user,
      reference=reference_b,
      saved_date=date,
      is_public=True)  
    
  def test_add_new_item_is_added(self):
    """
    Posting a bookmark will add it to the user's collection.
    """
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",password="pA"))
    # check presence of r1 reference
    resp = self.client.get(reverse("wom_user.views.user_collection",
                     kwargs={"owner_name":"uA"}))
    self.assertEqual(2,resp.context["user_bookmarks"].paginator.count)
    # mark the first reference as read.
    resp = self.add_request("uA",
                { "url": u"http://new/mouf",
                  "title": u"new title",
                  "comment": u"mouf",
                  "source_url": u"http://glop",
                  "source_title": u"new name",
                  })
    # resp_dic = simplejson.loads(resp.content)
    # self.assertEqual("success",resp_dic["status"])
    # check absence of r1 reference
    resp = self.client.get(reverse("wom_user.views.user_collection",
                     kwargs={"owner_name":"uA"}))
    self.assertEqual(3,resp.context["user_bookmarks"].paginator.count)
    items = resp.context["user_bookmarks"]
    new_b_candidates = [b for b in items \
                        if b.reference.url==u"http://new/mouf"]
    self.assertEqual(1, len(new_b_candidates))
    new_b = new_b_candidates[0]
    self.assertEqual(u"mouf",new_b.comment)
    self.assertEqual(u"new title",new_b.reference.title)
    self.assertEqual(1,len(new_b.reference.sources.all()))
    new_b_src = new_b.reference.sources.all()[0]
    self.assertEqual(u"http://glop",new_b_src.url)
    self.assertEqual(u"new name",new_b_src.title)
    
    
  def test_add_new_item_is_added_without_source(self):
    """
    Posting a bookmark without providing a source will
    add the bookmark correctly anyway.
    """
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",password="pA"))
    # check presence of r1 reference
    resp = self.client.get(reverse("wom_user.views.user_collection",
                     kwargs={"owner_name":"uA"}))
    self.assertEqual(2,resp.context["user_bookmarks"].paginator.count)
    # mark the first reference as read.
    resp = self.add_request("uA",
                { "url": u"http://new/mouf",
                  "title": u"new title",
                  "comment": u"mouf",
                  })
    # resp_dic = simplejson.loads(resp.content)
    # self.assertEqual("success",resp_dic["status"])
    # check absence of r1 reference
    resp = self.client.get(reverse("wom_user.views.user_collection",
                     kwargs={"owner_name":"uA"}))
    self.assertEqual(3,resp.context["user_bookmarks"].paginator.count)
    items = resp.context["user_bookmarks"]
    new_b_candidates = [b for b in items \
                        if b.reference.url==u"http://new/mouf"]
    self.assertEqual(1, len(new_b_candidates))
    new_b = new_b_candidates[0]
    self.assertEqual(u"mouf",new_b.comment)
    self.assertEqual(u"new title",new_b.reference.title)
    self.assertEqual(1,len(new_b.reference.sources.all()))
    new_b_src = new_b.reference.sources.all()[0]
    self.assertEqual(u"http://new",new_b_src.url)
    self.assertEqual(u"new",new_b_src.title)

  def test_add_new_item_is_added_with_url_only(self):
    """
    Posting a bookmark without providing anything but a url
    add the bookmark correctly anyway.
    """
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",password="pA"))
    # check presence of r1 reference
    resp = self.client.get(reverse("wom_user.views.user_collection",
                     kwargs={"owner_name":"uA"}))
    self.assertEqual(2,resp.context["user_bookmarks"].paginator.count)
    # mark the first reference as read.
    resp = self.add_request("uA", { "url": u"http://new/mouf"})
    # resp_dic = simplejson.loads(resp.content)
    # self.assertEqual("success",resp_dic["status"])
    # check absence of r1 reference
    resp = self.client.get(reverse("wom_user.views.user_collection",
                     kwargs={"owner_name":"uA"}))
    self.assertEqual(3,resp.context["user_bookmarks"].paginator.count)
    items = resp.context["user_bookmarks"]
    new_b_candidates = [b for b in items \
                        if b.reference.url==u"http://new/mouf"]
    self.assertEqual(1, len(new_b_candidates))
    new_b = new_b_candidates[0]
    self.assertEqual(u"",new_b.comment)
    self.assertEqual(u"new/mouf",new_b.reference.title)
    self.assertEqual(1,len(new_b.reference.sources.all()))
    new_b_src = new_b.reference.sources.all()[0]
    self.assertEqual(u"http://new",new_b_src.url)
    self.assertEqual(u"new",new_b_src.title)
    
  def test_add_new_item_is_added_with_existing_source_url(self):
    """
    Posting a bookmark with a source url that matches an
    exiting one, will associate the new bookmark with the existing
    source.
    """
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",password="pA"))
    # check presence of r1 reference
    resp = self.client.get(reverse("wom_user.views.user_collection",
                     kwargs={"owner_name":"uA"}))
    self.assertEqual(2,resp.context["user_bookmarks"].paginator.count)
    # mark the first reference as read.
    resp = self.add_request("uA",
                { "url": u"http://new/mouf",
                  "title": u"new title",
                  "comment": u"mouf",
                  "source_url": self.source.url,
                  })
    # resp_dic = simplejson.loads(resp.content)
    # self.assertEqual("success",resp_dic["status"])
    # check absence of r1 reference
    resp = self.client.get(reverse("wom_user.views.user_collection",
                     kwargs={"owner_name":"uA"}))
    self.assertEqual(3,resp.context["user_bookmarks"].paginator.count)
    items = resp.context["user_bookmarks"]
    self.assertIn(u"http://new/mouf",[b.reference.url for b in items])
    self.assertIn(u"mouf",[b.comment for b in items \
                           if b.reference.url==u"http://new/mouf"])
    self.assertEqual(self.source,
                     Reference\
                     .objects.get(url=u"http://new/mouf").sources.get())

  def test_add_new_item_is_added_with_existing_url(self):
    """
    Posting a bookmark with a url for which a bookmark already
    exists will update the bookmark's title and description.
    """
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",password="pA"))
    # check presence of r1 reference
    resp = self.client.get(reverse("wom_user.views.user_collection",
                     kwargs={"owner_name":"uA"}))
    self.assertEqual(2,resp.context["user_bookmarks"].paginator.count)
    # mark the first reference as read.
    resp = self.add_request("uA",
                { "url": self.bkm.reference.url,
                  "title": u"new title",
                  "comment": u"mouf",
                  })
    # resp_dic = simplejson.loads(resp.content)
    # self.assertEqual("success",resp_dic["status"])
    # check absence of r1 reference
    resp = self.client.get(reverse("wom_user.views.user_collection",
                     kwargs={"owner_name":"uA"}))
    self.assertEqual(2,resp.context["user_bookmarks"].paginator.count)
    self.assertIn(self.bkm.reference.url,
            [b.reference.url for b  in resp.context["user_bookmarks"]])
    self.assertEqual(1,
             Reference.objects\
               .filter(url=self.bkm.reference.url).count())
    r = Reference.objects.get(url=self.bkm.reference.url)
    self.assertEqual(self.source.url,r.sources.get().url)
    # The ref info hasn't changed
    self.assertEqual(u"glop",r.title)
    self.assertEqual(u"",r.description)
    self.assertEqual(u"new title: mouf",
                     UserBookmark.objects.get(reference=r).comment)
    
  def test_add_new_item_is_added_with_existing_url_other_source(self):
    """
    Posting a bookmark with a url for which a bookmark already
    exists will update the bookmark's title, description and source.
    """
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",password="pA"))
    # check presence of r1 reference
    resp = self.client.get(reverse("wom_user.views.user_collection",
                     kwargs={"owner_name":"uA"}))
    self.assertEqual(2,resp.context["user_bookmarks"].paginator.count)
    # mark the first reference as read.
    resp = self.add_request("uA",
                { "url": self.bkm.reference.url,
                  "title": u"new title",
                  "comment": u"mouf",
                  "source_url": u"http://barf",
                  })
    # resp_dic = simplejson.loads(resp.content)
    # self.assertEqual("success",resp_dic["status"])
    # check absence of r1 reference
    resp = self.client.get(reverse("wom_user.views.user_collection",
                     kwargs={"owner_name":"uA"}))
    self.assertEqual(2,resp.context["user_bookmarks"].paginator.count)
    self.assertIn(self.bkm.reference.url,
            [b.reference.url for b  in resp.context["user_bookmarks"]])
    self.assertEqual(1,
             Reference.objects\
               .filter(url=self.bkm.reference.url).count())
    r = Reference.objects.get(url=self.bkm.reference.url)
    # The source has not changed
    self.assertEqual(u"http://mouf",r.sources.get().url)
    # The ref info has not changed
    self.assertEqual(u"glop",r.title)
    self.assertEqual(u"",r.description)
    self.assertEqual(u"new title: mouf",
                     UserBookmark.objects.get(reference=r).comment)
    
  def test_add_new_item_is_added_with_existing_url_same_source(self):
    """
    Posting a bookmark with a url for which a bookmark already
    exists will update the bookmark's title, description and
    source's name if necessary.
    """
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",password="pA"))
    # check presence of r1 reference
    resp = self.client.get(reverse("wom_user.views.user_collection",
                     kwargs={"owner_name":"uA"}))
    self.assertEqual(2,resp.context["user_bookmarks"].paginator.count)
    # mark the first reference as read.
    resp = self.add_request("uA",
                { "url": self.bkm.reference.url,
                  "title": u"new title",
                  "comment": u"mouf",
                  "source_url": self.source.url,
                  "source_title": u"new name",
                  })
    # resp_dic = simplejson.loads(resp.content)
    # self.assertEqual("success",resp_dic["status"])
    # check absence of r1 reference
    resp = self.client.get(reverse("wom_user.views.user_collection",
                     kwargs={"owner_name":"uA"}))
    self.assertEqual(2,resp.context["user_bookmarks"].paginator.count)
    self.assertIn(self.bkm.reference.url,
            [b.reference.url for b  in resp.context["user_bookmarks"]])
    self.assertEqual(1,
             Reference.objects\
               .filter(url=self.bkm.reference.url).count())
    r = Reference.objects.get(url=self.bkm.reference.url)
    self.assertEqual(self.source.url,r.sources.get().url)
    # The source name has not changed
    self.assertEqual(u"mouf",r.sources.get().title)
    # The ref info has not changed
    self.assertEqual(u"glop",r.title)
    self.assertEqual(u"",r.description)
    self.assertEqual(u"new title: mouf",
                     UserBookmark.objects.get(reference=r).comment)
  
  def test_add_new_item_to_other_user_fails(self):
    """
    Posting a bookmark to another user's collection fails.
    """
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",password="pA"))
    # mark the first reference as read.
    self.add_request("uB",
             { "url": u"http://new/mouf",
               "title": u"new title",
               "comment": u"mouf",
               "source_url": u"http://glop",
               "source_title": u"new name",
               },
             expectedStatusCode=403)
    
  def test_add_new_item_with_same_url_as_its_sources_succeeds(self):
    """Posting a bookmark by giving the same url for the source and for
    the bookmark should succeed anyway.
    """
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",password="pA"))
    # mark the first reference as read.
    self.add_request("uA",
             { "url": u"http://samesame",
               "title": u"same title",
               "comment": u"same",
               "source_url": u"http://samesame",
               "source_title": u"same title",
               })
    resp = self.client.get(reverse("wom_user.views.user_collection",
                     kwargs={"owner_name":"uA"}))
    new_item_candidates = [b.reference for b in resp.context["user_bookmarks"] \
                           if b.reference.url == u"http://samesame"]
    self.assertEqual(1,len(new_item_candidates))
    new_item_reference = new_item_candidates[0]
    self.assertEqual(0,len(new_item_reference.sources.all()))
    

class UserCollectionViewTest(TestCase,UserBookmarkAddTestMixin):

  def setUp(self):
    UserBookmarkAddTestMixin.setUp(self)
  
  def add_request(self,username,optionsDict,expectedStatusCode=200):
    """
    Send the request as a JSON loaded POST.
    """
    resp = self.client.post(reverse("wom_user.views.user_collection",
                    kwargs={"owner_name":username}),
                simplejson.dumps(optionsDict),
                content_type="application/json")
    self.assertEqual(expectedStatusCode,resp.status_code)
    return resp
  
  def test_get_html_owner_returns_all(self):
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",
                      password="pA"))
    # request uA's collection
    resp = self.client.get(
      reverse("wom_user.views.user_collection",
          kwargs={"owner_name":"uA"}))
    self.assertEqual(200,resp.status_code)
    self.assertIn("collection.html",
            [t.name for t in resp.templates])
    self.assertIn("owner_name", resp.context)
    self.assertEqual("uA", resp.context["owner_name"])
    self.assertIn(u"user_bookmarks", resp.context)
    self.assertIn(u"num_bookmarks", resp.context)
    self.assertIn(u"collection_url", resp.context)
    self.assertIn(u"collection_add_bookmarklet", resp.context)
    self.assertEqual(2,resp.context["user_bookmarks"].paginator.count)
    self.assertEqual(2,len(resp.context["user_bookmarks"]))
    self.assertEqual(set([self.bkm,self.bkm_private]),
                     set(resp.context["user_bookmarks"]))
    # test additional attribute
    self.assertNotIn(False,[hasattr(b,"get_tag_names") \
                            for b in resp.context["user_bookmarks"]])
  
  def test_get_html_non_owner_logged_in_user_returns_all(self):
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",
                      password="pA"))
    # request uB's collection
    resp = self.client.get(
      reverse("wom_user.views.user_collection",
          kwargs={"owner_name":"uB"}))
    self.assertEqual(200,resp.status_code)
    self.assertIn("collection.html",
            [t.name for t in resp.templates])
    self.assertIn(u"user_bookmarks", resp.context)
    self.assertIn(u"num_bookmarks", resp.context)
    self.assertIn(u"collection_url", resp.context)
    self.assertIn(u"collection_add_bookmarklet", resp.context)
    self.assertEqual(1,resp.context["user_bookmarks"].paginator.count)
    self.assertEqual([self.bkm_b],
             list(resp.context["user_bookmarks"]))
 
  def test_get_html_anonymous_returns_all(self):
    # request uA's collection
    resp = self.client.get(
      reverse("wom_user.views.user_collection",
          kwargs={"owner_name":"uA"}))
    self.assertEqual(200,resp.status_code)
    self.assertIn("collection.html",
            [t.name for t in resp.templates])
    self.assertIn(u"user_bookmarks", resp.context)
    self.assertIn(u"num_bookmarks", resp.context)
    self.assertIn(u"collection_url", resp.context)
    self.assertIn(u"collection_add_bookmarklet", resp.context)
    self.assertEqual(1,resp.context["user_bookmarks"].paginator.count)
    self.assertEqual([self.bkm],
             list(resp.context["user_bookmarks"]))


class UserCollectionAddTest(TestCase,UserBookmarkAddTestMixin):
  
  def setUp(self):
    UserBookmarkAddTestMixin.setUp(self)
  
  def add_request(self,username,optionsDict,expectedStatusCode=302):
    """
    Send the request as a GET with some url parameters.
    """
    url = reverse("wom_user.views.user_collection_add",
            kwargs={"owner_name":username})\
            +"?"+"&".join(\
      "%s=%s" % t for t in optionsDict.items())
    url = url.replace(" ","%20")
    resp = self.client.get(url)
    self.assertEqual(expectedStatusCode,resp.status_code)
    return resp

  
class UserSourceAddTestMixin:
  """Mixin implementing the common tests for the Form and the REST API
  of source addition.
  """
  
  def add_request(self,url,optionsDict):
    """
    Returns the response that can be received from the input url.
    """
    raise NotImplementedError("This method should be reimplemented")

  def setUp(self):
    self.date = datetime.now(timezone.utc)
    self.source = Reference.objects.create(
      url=u"http://mouf",
      title=u"a mouf",
      pub_date=self.date)
    self.user = User.objects.create_user(username="uA",
                          password="pA")
    self.feed_source = Reference.objects.create(url="http://barf",
                                                title="a barf",
                                                pub_date=self.date)
    self.web_feed = WebFeed.objects.create(
      xmlURL="http://barf/bla.xml",
      last_update_check=self.date,
      source=self.feed_source)
    self.user_profile = UserProfile.objects.create(owner=self.user)
    self.user_profile.sources.add(self.source)
    self.user_profile.sources.add(self.feed_source)
    self.user_profile.web_feeds.add(self.web_feed)
    self.other_user = User.objects.create_user(username="uB",
                                               password="pB")
    self.other_profile = UserProfile.objects.create(owner=self.other_user)
    self.source_b = Reference.objects.create(
      url=u"http://glop",
      title=u"a glop",
      pub_date=self.date)
    self.other_profile.sources.add(self.source_b)

  def test_add_new_feed_source_to_owner(self):
    """
    WARNING: dependent on an internet access !
    """
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",
                      password="pA"))
    self.assertEqual(2,self.user_profile.sources.count())
    self.assertEqual(1,self.user_profile.web_feeds.count())
    new_feed_url = u"http://cyber.law.harvard.edu/rss/examples/rss2sample.xml"
    self.add_request("uA",
                     {"url": new_feed_url,
                      "feed_url": new_feed_url,
                      "title": u"a new"})
    self.assertEqual(3,self.user_profile.sources.count())
    self.assertEqual(2,self.user_profile.web_feeds.count())
    new_s_candidates = [
      s for s in self.user_profile.sources.all() \
      if s.url==new_feed_url]
    self.assertEqual(1, len(new_s_candidates))
    new_s = new_s_candidates[0]
    self.assertEqual(u"a new",new_s.title)
    self.assertEqual(new_feed_url,new_s.url)
    new_w = WebFeed.objects.get(source=new_s)
    self.assertEqual(new_feed_url,new_w.xmlURL)
    
  def test_add_new_feed_source_to_other_user_fails(self):
    """
    WARNING: dependent on an internet access !
    """
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",
                      password="pA"))
    self.assertEqual(2,self.user_profile.sources.count())
    self.assertEqual(1,self.user_profile.web_feeds.count())
    new_feed_url = u"http://cyber.law.harvard.edu/rss/examples/rss2sample.xml"
    self.add_request("uB",
             {"url": new_feed_url,
              "feed_url": new_feed_url,
              "name": u"a new"},
             expectedStatusCode=403)


class UserSourceViewTest(TestCase,UserSourceAddTestMixin):

  def setUp(self):
    UserSourceAddTestMixin.setUp(self)
  
  def add_request(self,username,optionsDict,expectedStatusCode=302):
    """
    Send the request as a JSON loaded POST (a redirect is expected
    in case of success).
    """
    resp = self.client.post(reverse("wom_user.views.user_river_sources",
                    kwargs={"owner_name":username}),
                simplejson.dumps(optionsDict),
                content_type="application/json")
    self.assertEqual(expectedStatusCode,resp.status_code)
    return resp
  
  def test_get_sources_for_owner(self):
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",
                      password="pA"))
    resp = self.client.get(reverse("wom_user.views.user_river_sources",
                     kwargs={"owner_name":"uA"}))
    self.assertEqual(200, resp.status_code)
    self.assertIn("visitor_name",resp.context)
    self.assertIn("source_add_bookmarklet",resp.context)
    self.assertIn("owner_name",resp.context)
    self.assertIn("tagged_web_feeds",resp.context)
    self.assertIn("other_sources",resp.context)
    self.assertEqual("uA",resp.context["owner_name"])
    self.assertEqual(1,len(resp.context["tagged_web_feeds"]))
    self.assertEqual("http://barf",resp.context["tagged_web_feeds"][0].source.url)
    self.assertEqual(1,len(resp.context["other_sources"]))
    self.assertEqual("http://mouf",resp.context["other_sources"][0].url)


class ImportUserBookmarksFromNSList(TestCase):

  def setUp(self):
    # Create a single reference with its source, and a user with a
    # single bookmark on this reference. Create also another user to
    # check for user data isolation.
    date = datetime.now(timezone.utc)
    self.source = Reference.objects.create(
      url=u"http://mouf",
      title=u"mouf",
      pub_date=date)
    reference = Reference.objects.create(
      url=u"http://mouf/a",
      title=u"glop",
      pub_date=date,
      save_count=1)
    reference.sources.add(self.source)
    reference.save_count += 1
    self.user = User.objects.create_user(username="uA",
                                         password="pA")
    self.user_profile = UserProfile.objects.create(owner=self.user)
    self.user_profile.sources.add(self.source)
    self.bkm = UserBookmark.objects.create(
        owner=self.user,
        reference=reference,
        saved_date=date)
    self.other_user = User.objects.create_user(username="uB",
                                               password="pB")
    nsbmk_txt = """\
<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<!-- This is an automatically generated file.
It will be read and overwritten.
Do Not Edit! -->
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
<DT><A HREF="http://www.example.com" ADD_DATE="1367951483" PRIVATE="1" TAGS="example,html">The example</A>
<DD>An example bookmark.
<DT><A HREF="http://mouf/a" ADD_DATE="1366828226" PRIVATE="0" TAGS="test">The mouf</A>
"""
    import_user_bookmarks_from_ns_list(self.user,nsbmk_txt)
  
  def test_bookmarks_are_added(self):
    self.assertEqual(2,self.user.userbookmark_set.count())
    bmk_urls = [b.reference.url for b in self.user.userbookmark_set.all()]
    self.assertIn("http://www.example.com",bmk_urls)
    self.assertIn("http://mouf/a",bmk_urls)
    self.assertEqual("The example",
                     UserBookmark.objects\
                     .get(reference__url="http://www.example.com")\
                     .reference.title)
    self.assertEqual("glop",
                     UserBookmark.objects\
                     .get(reference__url="http://mouf/a")\
                     .reference.title)
    
  def test_bookmarked_reference_save_count_updated(self):
    self.assertEqual(2,self.user.userbookmark_set.count())
    for b in self.user.userbookmark_set.all():
      self.assertEqual(1,b.reference.save_count,
                       "Wrong save count %s for %s" % (b.reference.save_count,
                                                       b.reference))
    
  def test_check_bookmarks_not_added_to_other_user(self):
    self.assertEqual(0,self.other_user.userbookmark_set.count())
  
  def test_check_tags_correctly_added(self):
    # Check that tags were added too
    self.assertTrue(Tag.objects.filter(name="example").exists())
    self.assertTrue(Tag.objects.filter(name="html").exists())
    self.assertTrue(Tag.objects.filter(name="test").exists())
    
  def test_check_tags_correctly_associated_to_bmks(self):
    # Check that tags were correctly associated with the bookmarks
    ref_tags = get_item_tag_names(self.user,
                                  Reference\
                                  .objects.get(url="http://www.example.com"))
    self.assertEqual(set(["example","html"]),set(ref_tags))
    ref_tags = get_item_tag_names(self.user,
                                  Reference\
                                  .objects.get(url="http://mouf/a"))
    self.assertEqual(set(["test"]),set(ref_tags))


class ImportUserFeedSourceFromOPMLTaskTest(TestCase):

  def setUp(self):
    # Create 2 users but only create sources for one of them.
    self.user = User.objects.create_user(username="uA",password="pA")
    self.user_profile = UserProfile.objects.create(owner=self.user)
    self.other_user = User.objects.create_user(username="uB",password="pB")
    self.other_user_profile = UserProfile.objects.create(owner=self.other_user)
    date = datetime.now(timezone.utc)
    r1 = Reference.objects.create(url="http://mouf",title="f1",pub_date=date)
    fs1 = WebFeed.objects.create(xmlURL="http://mouf/rss.xml",
                                 last_update_check=date,
                                 source=r1)
    r3 = Reference.objects.create(url="http://greuh",title="f3",pub_date=date)
    fs3 = WebFeed.objects.create(xmlURL="http://greuh/rss.xml",
                                 last_update_check=date,
                                 source=r3)
    self.user_profile.web_feeds.add(fs1)
    self.user_profile.web_feeds.add(fs3)
    self.user_profile.sources.add(r1)
    self.user_profile.sources.add(r3)
    # also add plain sources
    s1 = Reference.objects.create(url="http://s1",title="s1",pub_date=date)
    s3 = Reference.objects.create(url="http://s3",title="s3",pub_date=date)
    self.user_profile.sources.add(s1)
    self.user_profile.sources.add(s3)
    # create an opml snippet
    opml_txt = """\
<?xml version="1.0" encoding="UTF-8"?>
<opml version="1.0">
  <head>
  <title>My Subcriptions</title>
  </head>
  <body>
  <outline title="News" text="News">
    <outline text="Richard Stallman's Political Notes"
         title="Richard Stallman's Political Notes" type="rss"
         xmlUrl="http://stallman.org/rss/rss.xml" htmlUrl="http://stallman.org/archives/polnotes.html"/>
    <outline text="Mouf"
         title="Mouf" type="rss"
         xmlUrl="http://mouf/rss.xml" htmlUrl="http://mouf"/>
    <outline text="Dave&#39;s LifeLiner" title="Dave&#39;s LifeLiner"
         type="rss" xmlUrl="http://www.scripting.com/rss.xml" htmlUrl="http://scripting.com/"/>
  </outline>
  <outline title="Culture" text="Culture">
    <outline text="Open Culture" title="Open Culture" type="rss"
         xmlUrl="http://www.openculture.com/feed" htmlUrl="http://www.openculture.com"/>
  </outline>
  </body>
</opml>
"""
    import_user_feedsources_from_opml(self.user,opml_txt)
    
  def test_check_sources_correctly_added(self):
    self.assertEqual(7,self.user_profile.sources.count())
    self.assertEqual(5,self.user_profile.web_feeds.count())
    feed_urls = [s.xmlURL for s in self.user_profile.web_feeds.all()]
    self.assertIn("http://stallman.org/rss/rss.xml",feed_urls)
    self.assertIn("http://www.scripting.com/rss.xml",feed_urls)
    self.assertIn("http://www.openculture.com/feed",feed_urls)
    
  def test_check_sources_not_added_to_other_user(self):
    self.assertEqual(0,self.other_user_profile.sources.count())
    self.assertEqual(0,self.other_user_profile.web_feeds.count())
    
  def test_check_tags_correctly_added(self):
    # Check that tags were added too
    self.assertTrue(Tag.objects.filter(name="News").exists())
    self.assertTrue(Tag.objects.filter(name="Culture").exists())
    
  def test_check_tags_correctly_associated_to_sources(self):
    # Check that tags were correctly associated with the sources
    src_tags = get_item_tag_names(self.user,
      WebFeed.objects.get(source__url="http://scripting.com/"))
    self.assertEqual(["News"],src_tags)
    src_tags = get_item_tag_names(
      self.user,
      WebFeed.objects.get(
        source__url="http://stallman.org/archives/polnotes.html"))
    self.assertEqual(["News"],src_tags)
    src_tags = get_item_tag_names(
      self.user,
      WebFeed.objects.get(source__url="http://mouf"))
    self.assertEqual(["News"],src_tags)
    src_tags = get_item_tag_names(
      self.user,
      WebFeed.objects.get(source__url="http://www.openculture.com"))
    self.assertEqual(["Culture"],src_tags)

class UserRiverViewTest(TestCase):

    def setUp(self):
        # Create 2 users and 3 sources (1 exclusive to each and a
        # shared one) with more references than MAX_ITEM_PER_PAGE
        self.user1 = User.objects.create_user(username="uA",password="pA")
        user1_profile = UserProfile.objects.create(owner=self.user1)
        self.user2 = User.objects.create_user(username="uB",password="pB")
        user2_profile = UserProfile.objects.create(owner=self.user2)
        date = datetime.now(timezone.utc)
        r1 = Reference.objects.create(url="http://mouf",title="glop",pub_date=date)
        f1 = WebFeed.objects.create(xmlURL="http://mouf/rss.xml",
                                    last_update_check=date,
                                    source=r1)
        r2 = Reference.objects.create(url="http://bla",title="bla",pub_date=date)
        f2 = WebFeed.objects.create(xmlURL="http://bla/rss.xml",
                                    last_update_check=date,
                                    source=r2)
        r3 = Reference.objects.create(url="http://greuh",title="greuh",pub_date=date)
        f3 = WebFeed.objects.create(xmlURL="http://greuh/rss.xml",
                                    last_update_check=date,
                                    source=r3)
        user1_profile.web_feeds.add(f1)
        user1_profile.web_feeds.add(f3)
        user2_profile.web_feeds.add(f2)
        user2_profile.web_feeds.add(f3)
        num_items = MAX_ITEMS_PER_PAGE+1
        for i in range(num_items):
            date += timedelta(hours=1)
            r = Reference.objects.create(url="http://moufa%d"%i,title="s1r%d" % i,
                                         pub_date=date)#,source=s1
            r.sources.add(r1)
            r = Reference.objects.create(url="http://moufb%d"%i,title="s2r%d" % i,
                                         pub_date=date)#,source=s2
            r.sources.add(r2)
            r = Reference.objects.create(url="http://moufc%d"%i,title="s3r%d" % i,
                                         pub_date=date)#,source=s3
            r.sources.add(r3)
    
    def test_get_html_for_owner_returns_max_items_ordered_newest_first(self):
        """
        Make sure a user can see its river properly ordered
        """
        # login as uA and make sure it succeeds
        self.assertTrue(self.client.login(username="uA",password="pA"))
        # request uA's river
        resp = self.client.get(reverse("wom_user.views.user_river_view",
                                       kwargs={"owner_name":"uA"}))
        self.assertEqual(200,resp.status_code)
        self.assertIn("river.html",[t.name for t in resp.templates])
        self.assertIn("source_add_bookmarklet", resp.context)
        self.assertIn("news_items", resp.context)
        items = resp.context["news_items"]
        self.assertGreaterEqual(MAX_ITEMS_PER_PAGE,len(items))
        sourceNames = set(int(rust.reference.title[1]) for rust in items)
        self.assertItemsEqual(sourceNames,(1,3))
        referenceNumbers = [int(rust.reference.title[3:]) for rust in items]
        self.assertEqual(list(reversed(sorted(referenceNumbers))),referenceNumbers)
        
    def test_get_html_for_non_owner_logged_user_returns_max_items_ordered_newest_first(self):
        """
        Make sure a logged in user can see another user's river.
        """
        # login as uA and make sure it succeeds
        self.assertTrue(self.client.login(username="uA",password="pA"))
        # request uB's river
        resp = self.client.get(reverse("wom_user.views.user_river_view",
                                       kwargs={"owner_name":"uB"}))
        self.assertEqual(200,resp.status_code)
        self.assertIn("river.html",[t.name for t in resp.templates])
        self.assertIn("source_add_bookmarklet", resp.context)
        self.assertIn("news_items", resp.context)
        items = resp.context["news_items"]
        self.assertGreaterEqual(MAX_ITEMS_PER_PAGE,len(items))
        sourceNames = set(int(rust.reference.title[1]) for rust in items)
        self.assertItemsEqual(sourceNames,(2,3))
        referenceNumbers = [int(rust.reference.title[3:]) for rust in items]
        self.assertEqual(list(reversed(sorted(referenceNumbers))),referenceNumbers)
        
    def test_get_html_for_anonymous_returns_max_items_ordered_newest_first(self):
        """
        Make sure an anonymous (ie. not logged in) user can see a user's river.
        """
        # request uA's river without loging in.
        resp = self.client.get(reverse("wom_user.views.user_river_view",
                                       kwargs={"owner_name":"uA"}))
        self.assertEqual(200,resp.status_code)
        self.assertIn("river.html",[t.name for t in resp.templates])
        self.assertIn("source_add_bookmarklet", resp.context)
        self.assertIn("news_items", resp.context)
        items = resp.context["news_items"]
        self.assertGreaterEqual(MAX_ITEMS_PER_PAGE,len(items))
        self.assertLess(0,len(items))
        sourceNames = set(int(rust.reference.title[1]) for rust in items)
        self.assertItemsEqual(sourceNames,(1,3))
        referenceNumbers = [int(rust.reference.title[3:]) for rust in items]
        self.assertEqual(list(reversed(sorted(referenceNumbers))),referenceNumbers)


class UserSieveViewTest(TestCase):

    def setUp(self):
        # Create 2 users and 3 sources (1 exclusive to each and a
        # shared one) with more references than MAX_ITEM_PER_PAGE
        self.user1 = User.objects.create_user(username="uA",password="pA")
        user1_profile = UserProfile.objects.create(owner=self.user1)
        self.user2 = User.objects.create_user(username="uB",password="pB")
        user2_profile = UserProfile.objects.create(owner=self.user2)
        date = datetime.now(timezone.utc)
        self.s1 = Reference.objects.create(url="http://mouf",title="glop",pub_date=date)
        f1 = WebFeed.objects.create(xmlURL="http://mouf/rss.xml",
                                    last_update_check=date,
                                    source=self.s1)
        # Having a second feed for a same source caused a bug in
        # ReferenceUserStatus creation when collecting new References
        f1Category = WebFeed.objects.create(xmlURL="http://mouf/category/rss.xml",
                                            last_update_check=date,
                                            source=self.s1)
        self.s2 = Reference.objects.create(url="http://bla",title="bla",pub_date=date)
        f2 = WebFeed.objects.create(xmlURL="http://bla/rss.xml",
                                    last_update_check=date,
                                    source=self.s2)
        self.s3 = Reference.objects.create(url="http://greuh",title="greuh",pub_date=date)
        f3 = WebFeed.objects.create(xmlURL="http://greuh/rss.xml",
                                    last_update_check=date,
                                    source=self.s3)
        user1_profile.web_feeds.add(f1)
        user1_profile.web_feeds.add(f1Category)
        user1_profile.web_feeds.add(f3)
        user1_profile.sources.add(self.s1,self.s3)
        user2_profile.web_feeds.add(f2)
        user2_profile.web_feeds.add(f3)
        user2_profile.sources.add(self.s2,self.s3)
        self.num_items_per_source = MAX_ITEMS_PER_PAGE+1
        for i in range(self.num_items_per_source):
            date += timedelta(hours=1)
            if i==0:
              r = Reference.objects.create(url="http://r1",title="s1r%d" % i,
                                           pub_date=date)#,source=s1
              r.sources.add(self.s1)
              r = Reference.objects.create(url="http://r2",title="s2r%d" % i,
                                           pub_date=date)#,source=s2
              r.sources.add(self.s2)
              r = Reference.objects.create(url="http://r3",title="s3r%d" % i,
                                           pub_date=date)#,source=s3
              r.sources.add(self.s3)
            else:
              r = Reference.objects.create(url="http://r1%d" % i,title="s1r%d" % i,
                                           pub_date=date)#,source=s1
              r.sources.add(self.s1)
              r = Reference.objects.create(url="http://r2%d" % i,title="s2r%d" % i,
                                           pub_date=date)#,source=s2
              r.sources.add(self.s2)
              r = Reference.objects.create(url="http://r3%d" % i,title="s3r%d" % i,
                                           pub_date=date)#,source=s3
              r.sources.add(self.s3)
              

    def test_check_user_unread_feed_items(self):
      """Test that that unread items are correctly collected: just the
      right number and correctly saved in DB.
      """
      count = check_user_unread_feed_items(self.user1)
      self.assertEqual(2*self.num_items_per_source,count)
      self.assertEqual(count,ReferenceUserStatus.objects\
                       .filter(owner=self.user1).count())
      
    def test_get_html_for_owner_returns_max_items_ordered_oldest_first(self):
        """
        Make sure a user can see its river properly ordered
        """
        # login as uA and make sure it succeeds
        self.assertTrue(self.client.login(username="uA",password="pA"))
        # request uA's river
        resp = self.client.get(reverse("wom_user.views.user_river_sieve",
                                       kwargs={"owner_name":"uA"}))
        self.assertEqual(200,resp.status_code)
        self.assertIn("sieve.html",[t.name for t in resp.templates])
        self.assertIn("source_add_bookmarklet", resp.context)
        self.assertIn("user_collection_url", resp.context)
        self.assertIn("oldest_unread_references", resp.context)
        items = resp.context["oldest_unread_references"]
        self.assertGreaterEqual(MAX_ITEMS_PER_PAGE,len(items))
        self.assertEqual((False,),tuple(set([r.has_been_read for r in items])))
        rustTitles = set([int(r.reference.title[1]) for r in items])
        self.assertEqual(rustTitles,set((1,3)))
        referenceNumbers = [int(r.reference.title[3:]) for r in items]
        self.assertEqual(list(sorted(referenceNumbers)),referenceNumbers)
        for rust in items:
          expected_source = getattr(self,"s%d" % int(rust.reference.title[1]))
          self.assertEqual(expected_source,rust.main_source,"Wrong main source for %s" % rust)
        
    def test_get_html_for_non_owner_logged_user_is_forbidden(self):
        """
        Make sure a logged in user can see another user's river.
        """
        # login as uA and make sure it succeeds
        self.assertTrue(self.client.login(username="uA",password="pA"))
        # request uB's river
        resp = self.client.get(reverse("wom_user.views.user_river_sieve",
                                       kwargs={"owner_name":"uB"}))
        self.assertEqual(403,resp.status_code)        
        
    def test_get_html_for_anonymous_redirects_to_login(self):
        """
        Make sure an anonymous (ie. not logged) user can see a user's river.
        """
        # request uA's river without loging in.
        resp = self.client.get(reverse("wom_user.views.user_river_sieve",
                                       kwargs={"owner_name":"uA"}))
        self.assertEqual(302,resp.status_code)
        self.assertRegexpMatches(resp["Location"],
                                 "http://\\w+"
                                 + reverse('django.contrib.auth.views.login')
                                 + "\\?next="
                                 + reverse("wom_user.views.user_river_sieve",
                                           kwargs={"owner_name":"uA"}))
        
    def test_post_json_pick_item_out_of_sieve(self):
        """
        Make sure posting an item as read will remove it from the sieve.
        """
        # login as uA and make sure it succeeds
        self.assertTrue(self.client.login(username="uA",password="pA"))
        # check presence of r1 reference
        resp = self.client.get(reverse("wom_user.views.user_river_sieve",
                                       kwargs={"owner_name":"uA"}))
        items = resp.context["oldest_unread_references"]
        num_ref_r1 = [r.reference.url for r in items].count("http://r1")
        self.assertLessEqual(1,num_ref_r1)
        # mark the first reference as read.
        resp = self.client.post(reverse("wom_user.views.user_river_sieve",
                                        kwargs={"owner_name":"uA"}),
                                simplejson.dumps({"action":"read","references":["http://r1"]}),
                                content_type="application/json")
        self.assertEqual(200,resp.status_code)
        resp_dic = simplejson.loads(resp.content)
        self.assertEqual("read",resp_dic["action"])
        self.assertEqual("success",resp_dic["status"])
        self.assertLessEqual(num_ref_r1,resp_dic["count"])
        # check absence of r1 reference
        resp = self.client.get(reverse("wom_user.views.user_river_sieve",
                                       kwargs={"owner_name":"uA"}))
        items = resp.context["oldest_unread_references"]
        self.assertEqual(0,[r.reference.url for r in items].count("http://r1"))

    def test_post_json_pick_several_items_out_of_sieve(self):
        """
        Make sure posting a list of items as read will remove them from the sieve.
        """
        # login as uA and make sure it succeeds
        self.assertTrue(self.client.login(username="uA",password="pA"))
        # check presence of r1 reference
        resp = self.client.get(reverse("wom_user.views.user_river_sieve",
                                       kwargs={"owner_name":"uA"}))
        items = resp.context["oldest_unread_references"]
        num_ref_r1 = [r.reference.url for r in items].count("http://r1")
        self.assertLessEqual(1,num_ref_r1)
        num_ref_r3 = [r.reference.url for r in items].count("http://r3")
        self.assertLessEqual(1,num_ref_r3)
        # mark the first reference as read.
        resp = self.client.post(reverse("wom_user.views.user_river_sieve",
                                        kwargs={"owner_name":"uA"}),
                                simplejson.dumps({"action":"read",
                                                  "references":["http://r1","http://r3"]}),
                                content_type="application/json")
        self.assertEqual(200,resp.status_code)
        resp_dic = simplejson.loads(resp.content)
        self.assertEqual("read",resp_dic["action"])
        self.assertEqual("success",resp_dic["status"])
        self.assertLessEqual(num_ref_r1+num_ref_r3,resp_dic["count"])
        # check absence of r1 reference
        resp = self.client.get(reverse("wom_user.views.user_river_sieve",
                                       kwargs={"owner_name":"uA"}))
        items = resp.context["oldest_unread_references"]
        self.assertEqual(0,[r.reference.url for r in items].count("http://r1"))
        self.assertEqual(0,[r.reference.url for r in items].count("http://r3"))        

    def test_post_json_drop_sieve_content(self):
        """
        Make sure posting an item as read will remove it from the sieve.
        """
        # login as uA and make sure it succeeds
        self.assertTrue(self.client.login(username="uA",password="pA"))
        # check presence of r1 reference
        resp = self.client.get(reverse("wom_user.views.user_river_sieve",
                                       kwargs={"owner_name":"uA"}))
        items = resp.context["oldest_unread_references"]
        num_refs = len(items)
        self.assertGreaterEqual(num_refs, 1)
        # mark the first reference as read.
        resp = self.client.post(reverse("wom_user.views.user_river_sieve",
                                        kwargs={"owner_name":"uA"}),
                                simplejson.dumps({"action":"drop"}),
                                content_type="application/json")
        self.assertEqual(200,resp.status_code)
        resp_dic = simplejson.loads(resp.content)
        self.assertEqual("drop",resp_dic["action"])
        self.assertEqual("success",resp_dic["status"])
        self.assertLessEqual(num_refs,resp_dic["count"])
        # check emptyness of sieve
        resp = self.client.get(reverse("wom_user.views.user_river_sieve",
                                       kwargs={"owner_name":"uA"}))
        items = resp.context["oldest_unread_references"]
        self.assertEqual(0,len(items))
        
    def test_post_malformed_json_returns_error(self):
        """
        Make sure when the json is malformed an error that is not a server error is returned.
        """
        # login as uA and make sure it succeeds
        self.assertTrue(self.client.login(username="uA",password="pA"))
        # mark a of uB reference as read.
        resp = self.client.post(reverse("wom_user.views.user_river_sieve",
                                        kwargs={"owner_name":"uA"}),
                                "action=read,references=(http://r1)",
                                content_type="application/json")
        self.assertEqual(400,resp.status_code)

    def test_post_json_for_non_owner_logged_user_is_forbidden(self):
        """
        Make sure when the json is malformed an error that is not a server error is returned.
        """
        # login as uA and make sure it succeeds
        self.assertTrue(self.client.login(username="uA",password="pA"))
        # mark a of uB reference as read.
        resp = self.client.post(reverse("wom_user.views.user_river_sieve",
                                        kwargs={"owner_name":"uB"}),
                                simplejson.dumps({"action":"read","references":["http://r2"]}),
                                content_type="application/json")
        self.assertEqual(403,resp.status_code)

    def test_post_json_for_anonymous_redirects(self):
        """
        Make sure an anonymous (ie. not logged) user can see a user's river.
        """
        resp = self.client.post(reverse("wom_user.views.user_river_sieve",
                                        kwargs={"owner_name":"uA"}),
                                simplejson.dumps({"action":"read","references":["http://r1"]}),
                                content_type="application/json")
        self.assertEqual(302,resp.status_code)

        
class UserSourcesViewTest(TestCase):

    def setUp(self):
        # Create 2 users and 3 feed sources (1 exclusive to each and a
        # shared one) and 3 non-feed sources.
        self.user1 = User.objects.create_user(username="uA",password="pA")
        user1_profile = UserProfile.objects.create(owner=self.user1)
        self.user2 = User.objects.create_user(username="uB",password="pB")
        user2_profile = UserProfile.objects.create(owner=self.user2)
        date = datetime.now(timezone.utc)
        r1 = Reference.objects.create(url="http://mouf",title="f1",pub_date=date)
        f1 = WebFeed.objects.create(xmlURL="http://mouf/rss.xml",
                                    last_update_check=date,
                                    source=r1)
        r2 = Reference.objects.create(url="http://bla",title="f2",pub_date=date)
        f2 = WebFeed.objects.create(xmlURL="http://bla/rss.xml",
                                    last_update_check=date,
                                    source=r2)
        r3 = Reference.objects.create(url="http://greuh",title="f3",pub_date=date)
        f3 = WebFeed.objects.create(xmlURL="http://greuh/rss.xml",
                                    last_update_check=date,
                                    source=r3)
        user1_profile.web_feeds.add(f1)
        user1_profile.web_feeds.add(f3)
        user2_profile.web_feeds.add(f2)
        user2_profile.web_feeds.add(f3)
        user1_profile.sources.add(r1)
        user1_profile.sources.add(r3)
        user2_profile.sources.add(r2)
        user2_profile.sources.add(r3)
        # also add plain sources
        s1 = Reference.objects.create(url="http://s1",title="s1",pub_date=date)
        s2 = Reference.objects.create(url="http://s2",title="s2",pub_date=date)
        s3 = Reference.objects.create(url="http://s3",title="s3",pub_date=date)
        user1_profile.sources.add(s1)
        user1_profile.public_sources.add(s1)
        user1_profile.sources.add(s3)
        user2_profile.sources.add(s2)
        user2_profile.public_sources.add(s2)
        user2_profile.sources.add(s3)
        
    def test_get_html_for_owner_returns_separate_source_and_feed(self):
        """
        Make sure a user can see its sources in two categories.
        """
        # login as uA and make sure it succeeds
        self.assertTrue(self.client.login(username="uA",password="pA"))
        # request uA's river
        resp = self.client.get(reverse("wom_user.views.user_river_sources",
                                       kwargs={"owner_name":"uA"}))
        self.assertEqual(200,resp.status_code)
        self.assertIn("sources.html",[t.name for t in resp.templates])
        self.assertIn("source_add_bookmarklet", resp.context)
        self.assertIn("tagged_web_feeds", resp.context)
        self.assertIn("other_sources", resp.context)
        items = resp.context["other_sources"]
        sourceNames = set([int(s.title[1]) for s in items])
        self.assertEqual(sourceNames,set((1,3)))
        sourceTypes = set([s.title[0] for s in items])
        self.assertEqual(set(("s",)),sourceTypes)
        feed_items = resp.context["tagged_web_feeds"]
        feedNames = set([int(s.source.title[1]) for s in feed_items])
        self.assertEqual(feedNames,set((1,3)))
        feedTypes = set([s.source.title[0] for s in feed_items])
        self.assertEqual(set(("f",)),feedTypes)
        feedTags = set([s.main_tag_name for s in feed_items])
        self.assertEqual(set(("",)),feedTags)
        
    def test_get_html_for_non_owner_logged_user_returns_public_source_only(self):
        """
        Make sure a logged in user can see another user's sources.
        """
        # login as uA and make sure it succeeds
        self.assertTrue(self.client.login(username="uA",password="pA"))
        # request uB's river
        resp = self.client.get(reverse("wom_user.views.user_river_sources",
                                       kwargs={"owner_name":"uB"}))
        self.assertEqual(200,resp.status_code)
        self.assertIn("sources.html",[t.name for t in resp.templates])
        self.assertIn("source_add_bookmarklet", resp.context)
        self.assertIn("tagged_web_feeds", resp.context)
        self.assertIn("other_sources", resp.context)
        items = resp.context["other_sources"]
        sourceNames = set([int(s.title[1]) for s in items])
        self.assertEqual(sourceNames,set((2,)))
        sourceTypes = set([s.title[0] for s in items])
        self.assertEqual(set(("s",)),sourceTypes)
        # All feeds being systematically public they should all be
        # visible (NB: in practice the app guarantees that a source
        # associated to a feed is always public which is not the case
        # here with s3)
        feed_items = resp.context["tagged_web_feeds"]
        feedNames = set([int(s.source.title[1]) for s in feed_items])
        self.assertEqual(feedNames,set((2,3)))
        feedTypes = set([s.source.title[0] for s in feed_items])
        self.assertEqual(set(("f",)),feedTypes)
        
    def test_get_html_for_anonymous_returns_all_sources(self):
        """
        Make sure an anonymous user can see users' sources.
        """
        # request uA's river
        resp = self.client.get(reverse("wom_user.views.user_river_sources",
                                       kwargs={"owner_name":"uA"}))
        self.assertEqual(200,resp.status_code)
        self.assertIn("sources.html",[t.name for t in resp.templates])
        self.assertIn("source_add_bookmarklet", resp.context)
        self.assertIn("tagged_web_feeds", resp.context)
        self.assertIn("other_sources", resp.context)
        items = resp.context["other_sources"]
        sourceNames = set([int(s.title[1]) for s in items])
        self.assertEqual(sourceNames,set((1,)))
        sourceTypes = set([s.title[0] for s in items])
        self.assertEqual(set(("s",)),sourceTypes)
        # All feeds being systematically public they should all be
        # visible (NB: in practice the app guarantees that a source
        # associated to a feed is always public which is not the case
        # here with s3)
        feed_items = resp.context["tagged_web_feeds"]
        feedNames = set([int(s.source.title[1]) for s in feed_items])
        self.assertEqual(feedNames,set((1,3)))
        feedTypes = set([s.source.title[0] for s in feed_items])
        self.assertEqual(set(("f",)),feedTypes)
        
    def test_get_opml_for_anonymous_returns_all_sources(self):
        """
        Make sure an anonymous user can see users' sources as OPML.
        """
        # request uA's river
        resp = self.client.get(reverse("wom_user.views.user_river_sources",
                                       kwargs={"owner_name":"uA"})+"?format=opml")
        self.assertEqual(200,resp.status_code)
        self.assertIn("sources_opml.xml",[t.name for t in resp.templates])
        self.assertIn("tagged_web_feeds", resp.context)
        # All feeds being systematically public they should all be
        # visible (NB: in practice the app guarantees that a source
        # associated to a feed is always public which is not the case
        # here with s3)
        feed_items = resp.context["tagged_web_feeds"]
        feedNames = set([int(s.source.title[1]) for s in feed_items])
        self.assertEqual(feedNames,set((1,3)))
        feedTypes = set([s.source.title[0] for s in feed_items])
        self.assertEqual(set(("f",)),feedTypes)


class ReferenceUserStatusModelTest(TestCase):

  def setUp(self):
    self.date = datetime.now(timezone.utc)
    self.reference = Reference.objects.create(url="http://mouf",
                                              title="glop",
                                              pub_date=self.date)
    self.user = User.objects.create(username="name")
    
  def test_construction_defaults(self):
    """
    This tests just makes it possible to double check that a
    change in the default is voluntary.
    """
    s = Reference.objects.create(url="http://source",title="source",pub_date=self.date)
    rust = ReferenceUserStatus.objects.create(reference=self.reference,
                                              owner=self.user,
                                              reference_pub_date=self.date,
                                              main_source=s)
    self.assertFalse(rust.has_been_read)
    self.assertFalse(rust.has_been_saved)

  def test_disappear_when_reference_is_cleaned(self):
    src = self.reference
    ref = Reference(url="http://source",title="other",pub_date=self.date)
    ref.save()
    rust = ReferenceUserStatus(reference=ref,
                               owner=self.user,
                               reference_pub_date=self.date,
                               main_source=src)
    rust.save()
    self.assertTrue(Reference.objects.filter(title="other").exists())
    self.assertTrue(ReferenceUserStatus.objects.filter(main_source=src).exists())
    Reference.objects.filter(title="other").delete()
    self.assertFalse(Reference.objects.filter(title="other").exists())
    self.assertFalse(ReferenceUserStatus.objects.filter(main_source=src).exists())


class UserSourceItemViewTest(TestCase):
  """Test the single source view."""
  
  def setUp(self):
    self.date = datetime.now(timezone.utc)
    self.source = Reference.objects.create(
      url=u"http://mouf",
      title=u"a mouf",
      pub_date=self.date)
    self.user = User.objects.create_user(username="uA",
                          password="pA")
    self.feed_source = Reference.objects.create(url="http://barf",
                                                title="a barf",
                                                pub_date=self.date)
    self.web_feed = WebFeed.objects.create(
      xmlURL="http://barf/bla.xml",
      last_update_check=self.date,
      source=self.feed_source)
    self.user_profile = UserProfile.objects.create(owner=self.user)
    self.user_profile.sources.add(self.source)
    self.user_profile.sources.add(self.feed_source)
    self.user_profile.web_feeds.add(self.web_feed)
    self.other_user = User.objects.create_user(username="uB",
                                               password="pB")

  def change_request(self,username,source_url,optionsDict,expectedStatusCode=200):
    """
    Send the request as a JSON loaded POST.
    """
    resp = self.client.post(reverse("wom_user.views.user_river_source_item",
                                    kwargs={"owner_name":username,
                                            "source_url": source_url}),
                simplejson.dumps(optionsDict),
                content_type="application/json")
    self.assertEqual(expectedStatusCode,resp.status_code)
    return resp
    
  def test_get_html_user_source(self):
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",password="pA"))
    resp = self.client.get(reverse("wom_user.views.user_river_source_item",
                                   kwargs={"owner_name":"uA","source_url":self.source.url}))
    self.assertEqual(200,resp.status_code)
    self.assertIn("source_edit.html",[t.name for t in resp.templates])
    self.assertIn("ref_form", resp.context)
    self.assertIn("feed_forms", resp.context)
    self.assertEqual(0, len(resp.context["feed_forms"]))
    self.assertIn("ref_url", resp.context)
    self.assertEqual(self.source.url, resp.context["ref_url"])
    self.assertIn("ref_title", resp.context)
    self.assertEqual(self.source.title, resp.context["ref_title"])

  def test_get_html_other_user_source_is_forbidden(self):
    self.assertTrue(self.client.login(username="uB",password="pB"))
    resp = self.client.get(reverse("wom_user.views.user_river_source_item",
                                   kwargs={"owner_name":"uA","source_url":self.source.url}))
    self.assertEqual(403,resp.status_code)
    
  def test_get_html_user_source_with_feed_has_feed_forms_filled(self):
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",password="pA"))
    resp = self.client.get(reverse("wom_user.views.user_river_source_item",
                                   kwargs={"owner_name":"uA",
                                           "source_url":self.feed_source.url}))
    self.assertEqual(200,resp.status_code)
    self.assertIn("feed_forms", resp.context)
    self.assertEqual(1, len(resp.context["feed_forms"]))
    self.assertEqual(self.web_feed.xmlURL,resp.context["feed_forms"].keys()[0])

  def test_change_user_source_title_updates_title_in_db(self):
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",password="pA"))
    newTitle = self.source.title + "MOUF"
    self.change_request("uA",self.source.url,
                        {"ref-title": newTitle,
                         "ref-description": u"blah"}, 302)
    self.assertEqual(newTitle, Reference.objects.get(url=self.source.url).title)
    
  def test_change_user_source_title_updates_dont_mess_subscriptions(self):
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",password="pA"))
    formerFeedCount = self.user_profile.web_feeds.count()
    self.change_request("uA",self.feed_source.url,
                        {"ref-title": self.feed_source.title+"MOUF",
                         "ref-description": u"blah"}, 302)
    self.assertEqual(formerFeedCount, self.user_profile.web_feeds.count())
    
  def test_unsubscribe_from_feed(self):
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",password="pA"))
    self.assertEqual(1,self.user_profile.web_feeds.count())
    self.change_request("uA",self.feed_source.url,
                        {"feed0-follow": False}, 302)
    self.assertEqual(0, self.user_profile.web_feeds.count())
    self.assertEqual(1, WebFeed.objects.filter(xmlURL=self.web_feed.xmlURL).count())


class UserBookmarkViewTest(TestCase):
  """Test the bookmark view."""
  
  def setUp(self):
    self.date = datetime.now(timezone.utc)
    self.reference = Reference.objects.create(
      url=u"http://bla",
      title=u"a bla",
      pub_date=self.date)
    self.source1 = Reference.objects.create(
      url=u"http://blaSrc1",
      title=u"a source",
      pub_date=self.date)
    self.source2 = Reference.objects.create(
      url=u"http://blaSrc2",
      title=u"a source2",
      pub_date=self.date)
    self.reference.sources.add(self.source1)
    self.reference.sources.add(self.source2)
    self.user = User.objects.create_user(username="uA",
                                         password="pA")
    self.user_profile = UserProfile.objects.create(owner=self.user)
    self.bkm = UserBookmark.objects.create(
      owner=self.user,
      reference=self.reference,
      saved_date=self.date,
      is_public=True)
    set_item_tag_names(self.user, self.reference, ["T1","T2"])
    self.other_user = User.objects.create_user(username="uB",
                                               password="pB")

  def change_request(self,username,reference_url,optionsDict,expectedStatusCode=200):
    """
    Send the request as a JSON loaded POST.
    """
    resp = self.client.post(reverse("wom_user.views.user_collection_item",
                                    kwargs={"owner_name":username,
                                            "reference_url": reference_url}),
                simplejson.dumps(optionsDict),
                content_type="application/json")
    self.assertEqual(expectedStatusCode,resp.status_code)
    return resp
    
  def test_get_html_user_bookmark(self):
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",password="pA"))
    resp = self.client.get(reverse("wom_user.views.user_collection_item",
                                   kwargs={"owner_name":"uA",
                                           "reference_url":self.reference.url}))
    self.assertEqual(200,resp.status_code)
    self.assertIn("bookmark_edit.html",[t.name for t in resp.templates])
    self.assertIn("ref_form", resp.context)
    self.assertIn("bmk_form", resp.context)
    self.assertIn("ref_url", resp.context)
    self.assertEqual(self.reference.url, resp.context["ref_url"])
    self.assertIn("ref_title", resp.context)
    self.assertEqual(self.reference.title, resp.context["ref_title"])
    self.assertIn("ref_sources", resp.context)
    self.assertEqual([s for s in self.bkm.get_sources()],
                     [s for s in resp.context["ref_sources"]])
    self.assertIn("ref_tags", resp.context)
    self.assertEqual([n for n in self.bkm.get_tag_names()],
                     [n for n in resp.context["ref_tags"]])

  def test_get_html_other_user_bookmark_is_forbidden(self):
    self.assertTrue(self.client.login(username="uB",password="pB"))
    resp = self.client.get(reverse("wom_user.views.user_collection_item",
                                   kwargs={"owner_name":"uA",
                                           "reference_url":self.reference.url}))
    self.assertEqual(403,resp.status_code)
    
  def test_change_user_bookmark_title_updates_title_in_db(self):
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",password="pA"))
    newTitle = self.reference.title + "MOUF"
    self.change_request("uA",self.reference.url,
                        {"ref-title": newTitle,
                         "ref-description": u"blah"}, 302)
    self.assertEqual(newTitle,
                     Reference.objects.get(url=self.reference.url).title)
    
  def test_change_user_bookmark_comment_updates_comment_in_db(self):
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",password="pA"))
    newComment = self.bkm.comment + " NEW"
    self.change_request("uA",self.reference.url,
                        {"bmk-comment": newComment}, 302)
    self.assertEqual(newComment,
                     UserBookmark.objects.get(reference=self.reference).comment)

  def test_change_user_bookmark_privacy_updates_privacy_in_db(self):
    # login as uA and make sure it succeeds
    self.assertTrue(self.client.login(username="uA",password="pA"))
    newPrivacy = not self.bkm.is_public
    self.change_request("uA",self.reference.url,
                        {"bmk-is_public": newPrivacy}, 302)
    self.assertEqual(newPrivacy,
                     UserBookmark.objects.get(reference=self.reference).is_public)
