import logging

def setup_logger(log_level=logging.INFO):

    # Configure the logger so that we output to the terminal.
    logging.basicConfig(
        level=log_level, 
        format="%(levelname)s: %(message)s",  
        handlers=[logging.StreamHandler()] 
    )

    logger = logging.getLogger(__name__)

    logger.setLevel(log_level)

    return logger


# Create a default logger instance
logger = setup_logger()