import logging
import asyncio
from typing import List
from fastapi import WebSocket

class WebSocketLogHandler(logging.Handler):
    """
    Custom logging handler that broadcasts log messages via WebSocket.
    """
    
    def __init__(self, ws_manager):
        super().__init__()
        self.ws_manager = ws_manager
        self.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%H:%M:%S'
        ))
    
    def emit(self, record):
        """
        Emit a log record to all connected WebSocket clients.
        """
        try:
            msg = self.format(record)
            # Send to WebSocket manager in a non-blocking way
            # asyncio.create_task(self.ws_manager.broadcast({
            #     'type': 'log',
            #     'level': record.levelname,
            #     'message': msg,
            #     'timestamp': record.created
            # }))
        except Exception:
            self.handleError(record)

def setup_logging(ws_manager):
    """
    Configure logging to broadcast via WebSocket.
    """
    # Get root logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Add WebSocket handler
    ws_handler = WebSocketLogHandler(ws_manager)
    ws_handler.setLevel(logging.INFO)
    logger.addHandler(ws_handler)
    
    # Also add console handler for local debugging
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    logger.addHandler(console_handler)
    
    return logger
