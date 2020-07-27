import os, logging

logger = logging.getLogger(__name__)

data_dir=""
has_dir = False

data_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "data")
logger.debug("Looking for the database in the folder %s...", data_dir)

if not os.path.isdir(data_dir):
    logger.error("Charmorph data is not found at {}".format(data_dir))
else:
    has_dir=True
