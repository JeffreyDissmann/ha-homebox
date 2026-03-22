"""Constants for the HomeBox integration."""

from datetime import timedelta

DOMAIN = "homebox"
CONF_AREA = "area"
DEFAULT_NAME = "HomeBox"
CONF_LINKS = "links"
CONF_HA_DEVICE_TO_HB_ITEM = "ha_device_to_hb_item"
CONF_HB_ITEM_TO_HA_DEVICE = "hb_item_to_ha_device"
CONF_HB_ITEM_ID = "hb_item_id"
CONF_HA_DEVICE_ID = "ha_device_id"
CONF_HB_ITEM_DESCRIPTION = "hb_item_description"
CONF_HB_ITEM_IMAGE_URL = "hb_item_image_url"
CONF_HB_ITEM_MANUFACTURER = "hb_item_manufacturer"
CONF_HB_ITEM_MODEL_NUMBER = "hb_item_model_number"
CONF_HB_ITEM_SERIAL_NUMBER = "hb_item_serial_number"
CONF_HB_ITEM_NAME = "hb_item_name"
CONF_HB_ITEM_PURCHASE_PRICE = "hb_item_purchase_price"

LINK_TAG_NAME = "HomeAssistant"
LINK_BACKLINK_FIELD_NAME = "Home Assistant Device URL"

API_BASE_PATH = "/api"
DEFAULT_POLL_INTERVAL = timedelta(hours=1)
