from django.db import models

from wom_pebbles.models import Reference
from wom_pebbles.models import URL_MAX_LENGTH


class WebFeed(models.Model):
  """Represent a web feed (typically RSS or Atom) associated to a
  reference to be considered as a source of news items.
  """
  # Reference consdiered as the source
  source = models.ForeignKey(Reference)
  # The URL where to get updated list of References from
  xmlURL = models.CharField(max_length=URL_MAX_LENGTH)
  # Date marking the last time the source was checked for an update
  last_update_check = models.DateTimeField('last update')
      
