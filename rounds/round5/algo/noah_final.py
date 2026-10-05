from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Tuple, Any
import base64
import json
import math
import zlib


class Trader:
    """
    Round 5 combined hidden-bot event trader.

    Included category modules:
    - Oxygen v8
    - Snackpack v1
    - Galaxy v1
    - Microchips v1
    - Pebbles v1
    - Robots v1
    - Sleep Pods v1
    - UV Visors v1 using the uploaded aggressive RULES version, not the compact LEGS version.

    Core design:
    - public market trades are converted into fingerprints:
        source product + quantity + inferred side + state condition
    - selected scanner rules update target positions;
    - active L1 execution moves toward the target;
    - all persistence uses compressed traderData to stay below the platform state-size limit.

    Important:
    Run the Rust/local backtester with:
        --raw-csv-market-trades full
    Otherwise state.market_trades may not contain the public fingerprints.
    """

    LIMIT = 10
    MAX_HIST = 2200
    SNAP_KEEP = 10
    SEEN_KEEP = 2200
    DEBUG = False

    PRODUCTS = ['OXYGEN_SHAKE_MORNING_BREATH',
 'OXYGEN_SHAKE_EVENING_BREATH',
 'OXYGEN_SHAKE_MINT',
 'OXYGEN_SHAKE_CHOCOLATE',
 'OXYGEN_SHAKE_GARLIC',
 'SNACKPACK_CHOCOLATE',
 'SNACKPACK_VANILLA',
 'SNACKPACK_PISTACHIO',
 'SNACKPACK_STRAWBERRY',
 'SNACKPACK_RASPBERRY',
 'GALAXY_SOUNDS_DARK_MATTER',
 'GALAXY_SOUNDS_BLACK_HOLES',
 'GALAXY_SOUNDS_PLANETARY_RINGS',
 'GALAXY_SOUNDS_SOLAR_WINDS',
 'GALAXY_SOUNDS_SOLAR_FLAMES',
 'MICROCHIP_CIRCLE',
 'MICROCHIP_OVAL',
 'MICROCHIP_SQUARE',
 'MICROCHIP_RECTANGLE',
 'MICROCHIP_TRIANGLE',
 'PEBBLES_XS',
 'PEBBLES_S',
 'PEBBLES_M',
 'PEBBLES_L',
 'PEBBLES_XL',
 'ROBOT_VACUUMING',
 'ROBOT_MOPPING',
 'ROBOT_DISHES',
 'ROBOT_LAUNDRY',
 'ROBOT_IRONING',
 'SLEEP_POD_SUEDE',
 'SLEEP_POD_LAMB_WOOL',
 'SLEEP_POD_POLYESTER',
 'SLEEP_POD_NYLON',
 'SLEEP_POD_COTTON',
 'UV_VISOR_YELLOW',
 'UV_VISOR_AMBER',
 'UV_VISOR_ORANGE',
 'UV_VISOR_RED',
 'UV_VISOR_MAGENTA']

    PRODUCT_CATEGORY = {'OXYGEN_SHAKE_MORNING_BREATH': 'OXYGEN',
 'OXYGEN_SHAKE_EVENING_BREATH': 'OXYGEN',
 'OXYGEN_SHAKE_MINT': 'OXYGEN',
 'OXYGEN_SHAKE_CHOCOLATE': 'OXYGEN',
 'OXYGEN_SHAKE_GARLIC': 'OXYGEN',
 'SNACKPACK_CHOCOLATE': 'SNACKPACK',
 'SNACKPACK_VANILLA': 'SNACKPACK',
 'SNACKPACK_PISTACHIO': 'SNACKPACK',
 'SNACKPACK_STRAWBERRY': 'SNACKPACK',
 'SNACKPACK_RASPBERRY': 'SNACKPACK',
 'GALAXY_SOUNDS_DARK_MATTER': 'GALAXY',
 'GALAXY_SOUNDS_BLACK_HOLES': 'GALAXY',
 'GALAXY_SOUNDS_PLANETARY_RINGS': 'GALAXY',
 'GALAXY_SOUNDS_SOLAR_WINDS': 'GALAXY',
 'GALAXY_SOUNDS_SOLAR_FLAMES': 'GALAXY',
 'MICROCHIP_CIRCLE': 'MICROCHIPS',
 'MICROCHIP_OVAL': 'MICROCHIPS',
 'MICROCHIP_SQUARE': 'MICROCHIPS',
 'MICROCHIP_RECTANGLE': 'MICROCHIPS',
 'MICROCHIP_TRIANGLE': 'MICROCHIPS',
 'PEBBLES_XS': 'PEBBLES',
 'PEBBLES_S': 'PEBBLES',
 'PEBBLES_M': 'PEBBLES',
 'PEBBLES_L': 'PEBBLES',
 'PEBBLES_XL': 'PEBBLES',
 'ROBOT_VACUUMING': 'ROBOTS',
 'ROBOT_MOPPING': 'ROBOTS',
 'ROBOT_DISHES': 'ROBOTS',
 'ROBOT_LAUNDRY': 'ROBOTS',
 'ROBOT_IRONING': 'ROBOTS',
 'SLEEP_POD_SUEDE': 'SLEEP_PODS',
 'SLEEP_POD_LAMB_WOOL': 'SLEEP_PODS',
 'SLEEP_POD_POLYESTER': 'SLEEP_PODS',
 'SLEEP_POD_NYLON': 'SLEEP_PODS',
 'SLEEP_POD_COTTON': 'SLEEP_PODS',
 'UV_VISOR_YELLOW': 'UV_VISORS',
 'UV_VISOR_AMBER': 'UV_VISORS',
 'UV_VISOR_ORANGE': 'UV_VISORS',
 'UV_VISOR_RED': 'UV_VISORS',
 'UV_VISOR_MAGENTA': 'UV_VISORS'}

    RULES = {'OXYGEN_SHAKE_GARLIC': {'long_entry': [('OXYGEN_SHAKE_GARLIC', 4, 'BUY', 'ANY'),
                                        ('OXYGEN_SHAKE_MINT', 4, 'BUY', 'ANY')],
                         'long_exit': [('OXYGEN_SHAKE_GARLIC', 4, 'SELL', 'RUN_HIGH20')],
                         'short_entry': [('OXYGEN_SHAKE_GARLIC', 4, 'SELL', 'ROLL500_HIGH20')],
                         'short_exit': [('OXYGEN_SHAKE_GARLIC', 1, 'BUY', 'ANY'),
                                        ('OXYGEN_SHAKE_MINT', 1, 'BUY', 'ANY')]},
 'OXYGEN_SHAKE_CHOCOLATE': {'long_entry': [('OXYGEN_SHAKE_GARLIC', 3, 'SELL', 'RUN_HIGH20'),
                                           ('OXYGEN_SHAKE_EVENING_BREATH', 3, 'SELL', 'MIDZ200_LOW')],
                            'long_exit': [('OXYGEN_SHAKE_MORNING_BREATH', 3, 'SELL', 'MIDZ2000_LOW')],
                            'short_entry': [('OXYGEN_SHAKE_MORNING_BREATH', 1, 'BUY', 'ROLL1000_HIGH20')],
                            'short_exit': [('OXYGEN_SHAKE_EVENING_BREATH', 2, 'SELL', 'MIDZ1000_LOW')]},
 'OXYGEN_SHAKE_MINT': {'long_entry': [('OXYGEN_SHAKE_GARLIC', 2, 'BUY', 'RUN_HIGH20')],
                       'long_exit': [('OXYGEN_SHAKE_GARLIC', 4, 'BUY', 'MIDZ2000_HIGH')],
                       'short_entry': [('OXYGEN_SHAKE_MORNING_BREATH', 3, 'SELL', 'MIDZ2000_LOW'),
                                       ('OXYGEN_SHAKE_GARLIC', 3, 'SELL', 'ROLL500_HIGH20')],
                       'short_exit': [('OXYGEN_SHAKE_MORNING_BREATH', 1, 'SELL', 'MIDZ2000_HIGH')]},
 'OXYGEN_SHAKE_MORNING_BREATH': {'long_entry': [('OXYGEN_SHAKE_GARLIC', 2, 'SELL', 'RUN_HIGH20'),
                                                ('OXYGEN_SHAKE_MORNING_BREATH', 2, 'SELL', 'RUN_LOW20')],
                                 'long_exit': [('OXYGEN_SHAKE_MINT', 3, 'SELL', 'MIDZ200_HIGH')],
                                 'short_entry': [('OXYGEN_SHAKE_CHOCOLATE', 1, 'BUY', 'ROLL2000_LOW20')],
                                 'short_exit': [('OXYGEN_SHAKE_GARLIC', 4, 'SELL', 'RUN_HIGH20')]},
 'OXYGEN_SHAKE_EVENING_BREATH': {'long_entry': [('OXYGEN_SHAKE_MORNING_BREATH', 1, 'BUY', 'RUN_LOW20'),
                                                ('OXYGEN_SHAKE_EVENING_BREATH', 1, 'BUY', 'MIDZ2000_LOW')],
                                 'long_exit': [('OXYGEN_SHAKE_GARLIC', 4, 'BUY', 'MIDZ1000_HIGH')],
                                 'short_entry': [('OXYGEN_SHAKE_GARLIC', 1, 'BUY', 'RUN_HIGH20'),
                                                 ('OXYGEN_SHAKE_GARLIC', 1, 'BUY', 'ROLL1000_HIGH20')],
                                 'short_exit': [('OXYGEN_SHAKE_GARLIC', 2, 'SELL', 'MIDZ1000_HIGH')]},
 'SNACKPACK_RASPBERRY': {'mode': 'full',
                         'long_entry': [('SNACKPACK_STRAWBERRY', 4, 'SELL', 'MIDZ2000_HIGH'),
                                        ('SNACKPACK_RASPBERRY', 4, 'SELL', 'MIDZ1000_LOW')],
                         'long_exit': [('SNACKPACK_CHOCOLATE', 4, 'SELL', 'MIDZ200_HIGH')],
                         'short_entry': [('SNACKPACK_RASPBERRY', 1, 'BUY', 'RUN_HIGH20')],
                         'short_exit': [('SNACKPACK_PISTACHIO', 2, 'SELL', 'MIDZ200_HIGH')]},
 'SNACKPACK_STRAWBERRY': {'mode': 'full',
                          'long_entry': [('SNACKPACK_RASPBERRY', 1, 'SELL', 'MIDZ1000_HIGH')],
                          'long_exit': [('SNACKPACK_STRAWBERRY', 1, 'BUY', 'MIDZ2000_HIGH')],
                          'short_entry': [('SNACKPACK_PISTACHIO', 1, 'SELL', 'ROLL2000_HIGH20')],
                          'short_exit': [('SNACKPACK_PISTACHIO', 1, 'SELL', 'MIDZ200_LOW')]},
 'SNACKPACK_PISTACHIO': {'mode': 'full',
                         'long_entry': [('SNACKPACK_RASPBERRY', 1, 'BUY', 'RUN_HIGH20')],
                         'long_exit': [('SNACKPACK_STRAWBERRY', 2, 'SELL', 'MIDZ200_HIGH')],
                         'short_entry': [('SNACKPACK_STRAWBERRY', 4, 'SELL', 'RUN_HIGH20')],
                         'short_exit': [('SNACKPACK_VANILLA', 4, 'SELL', 'MIDZ200_LOW')]},
 'SNACKPACK_CHOCOLATE': {'mode': 'full',
                         'long_entry': [('SNACKPACK_PISTACHIO', 1, 'SELL', 'MIDZ200_LOW'),
                                        ('SNACKPACK_VANILLA', 1, 'SELL', 'MIDZ2000_HIGH')],
                         'long_exit': [('SNACKPACK_RASPBERRY', 1, 'BUY', 'MIDZ500_LOW')],
                         'short_entry': [('SNACKPACK_CHOCOLATE', 1, 'SELL', 'ROLL2000_HIGH20')],
                         'short_exit': [('SNACKPACK_RASPBERRY', 3, 'BUY', 'MIDZ1000_HIGH')]},
 'SNACKPACK_VANILLA': {'mode': 'full',
                       'long_entry': [('SNACKPACK_VANILLA', 4, 'BUY', 'ROLL2000_LOW20')],
                       'long_exit': [('SNACKPACK_VANILLA', 4, 'SELL', 'MIDZ200_LOW')],
                       'short_entry': [('SNACKPACK_PISTACHIO', 1, 'SELL', 'ROLL200_LOW20'),
                                       ('SNACKPACK_VANILLA', 1, 'SELL', 'MIDZ2000_HIGH')],
                       'short_exit': [('SNACKPACK_VANILLA', 1, 'BUY', 'MIDZ200_HIGH')]},
 'GALAXY_SOUNDS_BLACK_HOLES': {'long_entry': [('GALAXY_SOUNDS_SOLAR_WINDS', 4, 'BUY', 'MIDZ500_HIGH')],
                               'long_exit': [('GALAXY_SOUNDS_SOLAR_FLAMES', 4, 'BUY', 'MIDZ200_HIGH')],
                               'short_entry': [('GALAXY_SOUNDS_BLACK_HOLES', 2, 'SELL', 'RUN_HIGH20'),
                                               ('GALAXY_SOUNDS_SOLAR_WINDS', 2, 'SELL', 'MIDZ200_HIGH')],
                               'short_exit': [('GALAXY_SOUNDS_BLACK_HOLES', 4, 'BUY', 'ROLL200_HIGH20')]},
 'GALAXY_SOUNDS_DARK_MATTER': {'long_entry': [('GALAXY_SOUNDS_BLACK_HOLES', 4, 'BUY', 'MIDZ2000_HIGH')],
                               'long_exit': [('GALAXY_SOUNDS_SOLAR_FLAMES', 1, 'BUY', 'MIDZ1000_LOW')],
                               'short_entry': [('GALAXY_SOUNDS_DARK_MATTER', 1, 'BUY', 'ROLL200_HIGH20')],
                               'short_exit': [('GALAXY_SOUNDS_SOLAR_WINDS', 2, 'SELL', 'MIDZ200_HIGH')]},
 'GALAXY_SOUNDS_PLANETARY_RINGS': {'long_entry': [('GALAXY_SOUNDS_BLACK_HOLES', 4, 'SELL', 'ROLL500_HIGH20')],
                                   'long_exit': [('GALAXY_SOUNDS_BLACK_HOLES', 1, 'BUY', 'MIDZ1000_HIGH')],
                                   'short_entry': [('GALAXY_SOUNDS_SOLAR_FLAMES', 4, 'BUY', 'MIDZ2000_HIGH'),
                                                   ('GALAXY_SOUNDS_SOLAR_WINDS', 4, 'BUY', 'ROLL500_HIGH20')],
                                   'short_exit': [('GALAXY_SOUNDS_BLACK_HOLES', 2, 'SELL', 'RUN_HIGH20')]},
 'GALAXY_SOUNDS_SOLAR_FLAMES': {'long_entry': [('GALAXY_SOUNDS_BLACK_HOLES', 1, 'SELL', 'MIDZ1000_HIGH'),
                                               ('GALAXY_SOUNDS_SOLAR_FLAMES', 1, 'SELL', 'MIDZ500_HIGH')],
                                'long_exit': [('GALAXY_SOUNDS_BLACK_HOLES', 2, 'BUY', 'MIDZ500_HIGH')],
                                'short_entry': [('GALAXY_SOUNDS_SOLAR_WINDS', 4, 'SELL', 'ROLL1000_HIGH20')],
                                'short_exit': [('GALAXY_SOUNDS_SOLAR_FLAMES', 4, 'BUY', 'MIDZ200_HIGH')]},
 'GALAXY_SOUNDS_SOLAR_WINDS': {'long_entry': [('GALAXY_SOUNDS_SOLAR_WINDS', 4, 'BUY', 'MIDZ500_HIGH'),
                                              ('GALAXY_SOUNDS_SOLAR_FLAMES', 4, 'BUY', 'RUN_HIGH20')],
                               'long_exit': [('GALAXY_SOUNDS_BLACK_HOLES', 1, 'SELL', 'RUN_HIGH20')],
                               'short_entry': [('GALAXY_SOUNDS_BLACK_HOLES', 1, 'SELL', 'ROLL1000_HIGH20')],
                               'short_exit': [('GALAXY_SOUNDS_SOLAR_FLAMES', 4, 'BUY', 'MIDZ200_HIGH')]},
 'MICROCHIP_CIRCLE': {'long_entry': [('MICROCHIP_OVAL', 2, 'SELL', 'MIDZ2000_LOW'),
                                     ('MICROCHIP_TRIANGLE', 2, 'SELL', 'RUN_LOW20')],
                      'long_exit': [('MICROCHIP_SQUARE', 2, 'SELL', 'MIDZ500_HIGH')],
                      'short_entry': [('MICROCHIP_SQUARE', 2, 'SELL', 'ROLL500_HIGH20')],
                      'short_exit': [('MICROCHIP_SQUARE', 1, 'SELL', 'MIDZ1000_HIGH')]},
 'MICROCHIP_OVAL': {'long_entry': [('MICROCHIP_OVAL', 2, 'BUY', 'ROLL500_LOW20'),
                                   ('MICROCHIP_SQUARE', 2, 'BUY', 'MIDZ1000_HIGH')],
                    'long_exit': [('MICROCHIP_TRIANGLE', 2, 'BUY', 'ANY')],
                    'short_entry': [('MICROCHIP_OVAL', 2, 'SELL', 'ROLL200_HIGH20')],
                    'short_exit': [('MICROCHIP_SQUARE', 1, 'BUY', 'RUN_HIGH20')]},
 'MICROCHIP_RECTANGLE': {'long_entry': [('MICROCHIP_RECTANGLE', 1, 'BUY', 'RUN_LOW20')],
                         'long_exit': [('MICROCHIP_SQUARE', 2, 'SELL', 'ROLL500_HIGH20')],
                         'short_entry': [('MICROCHIP_SQUARE', 2, 'SELL', 'ROLL200_HIGH20')],
                         'short_exit': [('MICROCHIP_OVAL', 3, 'SELL', 'MIDZ500_LOW')]},
 'MICROCHIP_SQUARE': {'long_entry': [('MICROCHIP_RECTANGLE', 2, 'SELL', 'RUN_LOW20')],
                      'long_exit': [('MICROCHIP_SQUARE', 2, 'SELL', 'RUN_HIGH20')],
                      'short_entry': [('MICROCHIP_RECTANGLE', 1, 'SELL', 'MIDZ200_HIGH'),
                                      ('MICROCHIP_SQUARE', 1, 'SELL', 'MIDZ2000_HIGH')],
                      'short_exit': [('MICROCHIP_OVAL', 3, 'BUY', 'ROLL2000_LOW20')]},
 'MICROCHIP_TRIANGLE': {'long_entry': [('MICROCHIP_TRIANGLE', 2, 'BUY', 'ROLL2000_LOW20')],
                        'long_exit': [('MICROCHIP_SQUARE', 1, 'BUY', 'ANY')],
                        'short_entry': [('MICROCHIP_TRIANGLE', 1, 'BUY', 'ROLL200_HIGH20')],
                        'short_exit': [('MICROCHIP_TRIANGLE', 1, 'SELL', 'RUN_LOW20')]},
 'PEBBLES_XL': {'long_entry': [('PEBBLES_XL', 2, 'SELL', 'ROLL500_HIGH20')],
                'long_exit': [('PEBBLES_L', 5, 'SELL', 'MIDZ2000_HIGH')],
                'short_entry': [('PEBBLES_M', 3, 'SELL', 'MIDZ200_LOW'), ('PEBBLES_S', 3, 'SELL', 'MIDZ2000_LOW')],
                'short_exit': [('PEBBLES_M', 3, 'SELL', 'MIDZ200_LOW')]},
 'PEBBLES_XS': {'long_entry': [],
                'long_exit': [],
                'short_entry': [('PEBBLES_XS', 4, 'SELL', 'MIDZ200_HIGH')],
                'short_exit': [('PEBBLES_S', 3, 'SELL', 'MIDZ2000_LOW')]},
 'PEBBLES_S': {'long_entry': [('PEBBLES_S', 5, 'SELL', 'MIDZ1000_LOW'), ('PEBBLES_S', 5, 'SELL', 'RUN_LOW20')],
               'long_exit': [('PEBBLES_XS', 4, 'BUY', 'MIDZ1000_LOW')],
               'short_entry': [('PEBBLES_XS', 4, 'BUY', 'RUN_LOW20'), ('PEBBLES_XL', 4, 'BUY', 'RUN_HIGH20')],
               'short_exit': [('PEBBLES_XL', 3, 'SELL', 'MIDZ2000_HIGH')]},
 'PEBBLES_M': {'long_entry': [('PEBBLES_M', 4, 'BUY', 'MIDZ2000_LOW')],
               'long_exit': [('PEBBLES_XL', 5, 'BUY', 'RUN_HIGH20')],
               'short_entry': [('PEBBLES_L', 3, 'SELL', 'MIDZ200_LOW')],
               'short_exit': [('PEBBLES_XS', 5, 'BUY', 'MIDZ1000_LOW')]},
 'PEBBLES_L': {'long_entry': [('PEBBLES_S', 3, 'SELL', 'MIDZ2000_LOW'), ('PEBBLES_XL', 3, 'SELL', 'ROLL200_HIGH20')],
               'long_exit': [('PEBBLES_XL', 3, 'SELL', 'ROLL200_HIGH20')],
               'short_entry': [('PEBBLES_S', 2, 'BUY', 'ROLL500_HIGH20')],
               'short_exit': [('PEBBLES_S', 5, 'SELL', 'MIDZ2000_LOW')]},
 'ROBOT_DISHES': {'long_entry': [('ROBOT_VACUUMING', 3, 'BUY', 'ROLL1000_HIGH20')],
                  'long_exit': [('ROBOT_IRONING', 2, 'SELL', 'ROLL2000_LOW20')],
                  'short_entry': [('ROBOT_MOPPING', 4, 'BUY', 'MIDZ500_HIGH'),
                                  ('ROBOT_MOPPING', 4, 'BUY', 'RUN_HIGH20')],
                  'short_exit': [('ROBOT_MOPPING', 2, 'SELL', 'RUN_HIGH20')]},
 'ROBOT_IRONING': {'long_entry': [],
                   'long_exit': [],
                   'short_entry': [('ROBOT_MOPPING', 4, 'SELL', 'ROLL1000_HIGH20')],
                   'short_exit': [('ROBOT_IRONING', 4, 'SELL', 'RUN_LOW20')]},
 'ROBOT_LAUNDRY': {'long_entry': [('ROBOT_MOPPING', 1, 'BUY', 'MIDZ1000_HIGH'),
                                  ('ROBOT_IRONING', 1, 'BUY', 'ROLL500_LOW20')],
                   'long_exit': [('ROBOT_LAUNDRY', 1, 'BUY', 'ANY')],
                   'short_entry': [('ROBOT_LAUNDRY', 1, 'BUY', 'RUN_HIGH20')],
                   'short_exit': [('ROBOT_IRONING', 2, 'SELL', 'ROLL500_LOW20')]},
 'ROBOT_MOPPING': {'long_entry': [('ROBOT_DISHES', 3, 'BUY', 'MIDZ500_HIGH')],
                   'long_exit': [('ROBOT_IRONING', 3, 'SELL', 'MIDZ200_LOW')],
                   'short_entry': [('ROBOT_LAUNDRY', 2, 'BUY', 'ROLL2000_LOW20')],
                   'short_exit': [('ROBOT_IRONING', 2, 'SELL', 'MIDZ200_LOW')]},
 'ROBOT_VACUUMING': {'long_entry': [('ROBOT_IRONING', 1, 'BUY', 'MIDZ500_LOW'),
                                    ('ROBOT_VACUUMING', 1, 'BUY', 'MIDZ2000_LOW')],
                     'long_exit': [('ROBOT_LAUNDRY', 1, 'BUY', 'ANY')],
                     'short_entry': [('ROBOT_VACUUMING', 2, 'SELL', 'MIDZ200_LOW')],
                     'short_exit': [('ROBOT_IRONING', 3, 'SELL', 'MIDZ200_LOW')]},
 'SLEEP_POD_SUEDE': {'long_entry': [('SLEEP_POD_POLYESTER', 4, 'SELL', 'RUN_HIGH20')],
                     'long_exit': [('SLEEP_POD_SUEDE', 4, 'SELL', 'RUN_HIGH20')],
                     'short_entry': [('SLEEP_POD_COTTON', 4, 'BUY', 'MIDZ1000_HIGH'),
                                     ('SLEEP_POD_SUEDE', 4, 'BUY', 'MIDZ2000_HIGH')],
                     'short_exit': [('SLEEP_POD_LAMB_WOOL', 3, 'SELL', 'MIDZ500_HIGH')]},
 'SLEEP_POD_LAMB_WOOL': {'long_entry': [('SLEEP_POD_LAMB_WOOL', 1, 'SELL', 'MIDZ500_HIGH'),
                                        ('SLEEP_POD_SUEDE', 1, 'SELL', 'MIDZ200_HIGH')],
                         'long_exit': [('SLEEP_POD_POLYESTER', 3, 'SELL', 'MIDZ2000_HIGH')],
                         'short_entry': [('SLEEP_POD_POLYESTER', 3, 'SELL', 'MIDZ200_LOW')],
                         'short_exit': [('SLEEP_POD_POLYESTER', 1, 'BUY', 'MIDZ500_HIGH')]},
 'SLEEP_POD_POLYESTER': {'long_entry': [('SLEEP_POD_NYLON', 4, 'BUY', 'MIDZ1000_LOW')],
                         'long_exit': [('SLEEP_POD_POLYESTER', 3, 'SELL', 'MIDZ2000_HIGH')],
                         'short_entry': [('SLEEP_POD_POLYESTER', 3, 'SELL', 'MIDZ200_HIGH'),
                                         ('SLEEP_POD_NYLON', 3, 'SELL', 'ROLL200_HIGH20')],
                         'short_exit': [('SLEEP_POD_COTTON', 1, 'BUY', 'MIDZ1000_HIGH')]},
 'SLEEP_POD_NYLON': {'long_entry': [('SLEEP_POD_NYLON', 1, 'BUY', 'ROLL2000_LOW20')],
                     'long_exit': [('SLEEP_POD_LAMB_WOOL', 2, 'SELL', 'ROLL200_HIGH20')],
                     'short_entry': [],
                     'short_exit': []},
 'SLEEP_POD_COTTON': {'long_entry': [('SLEEP_POD_SUEDE', 3, 'BUY', 'MIDZ2000_LOW')],
                      'long_exit': [('SLEEP_POD_SUEDE', 2, 'SELL', 'MIDZ1000_HIGH')],
                      'short_entry': [('SLEEP_POD_COTTON', 1, 'SELL', 'RUN_HIGH20'),
                                      ('SLEEP_POD_LAMB_WOOL', 1, 'SELL', 'MIDZ500_HIGH')],
                      'short_exit': [('SLEEP_POD_COTTON', 3, 'SELL', 'MIDZ1000_HIGH')]},
 'UV_VISOR_AMBER': {'long_entry': [],
                    'long_exit': [],
                    'short_entry': [('UV_VISOR_AMBER', 3, 'SELL', 'ANY')],
                    'short_exit': [('UV_VISOR_RED', 2, 'SELL', 'RUN_HIGH20')]},
 'UV_VISOR_MAGENTA': {'long_entry': [('UV_VISOR_MAGENTA', 3, 'BUY', 'ROLL2000_LOW20')],
                      'long_exit': [('UV_VISOR_RED', 2, 'SELL', 'MIDZ200_HIGH')],
                      'short_entry': [('UV_VISOR_AMBER', 3, 'BUY', 'MIDZ2000_LOW'),
                                      ('UV_VISOR_AMBER', 3, 'BUY', 'ROLL1000_LOW20')],
                      'short_exit': [('UV_VISOR_YELLOW', 1, 'SELL', 'ANY')]},
 'UV_VISOR_ORANGE': {'long_entry': [('UV_VISOR_AMBER', 4, 'SELL', 'ROLL2000_LOW20'),
                                    ('UV_VISOR_RED', 4, 'SELL', 'MIDZ1000_HIGH')],
                     'long_exit': [('UV_VISOR_AMBER', 1, 'BUY', 'MIDZ2000_LOW')],
                     'short_entry': [('UV_VISOR_YELLOW', 1, 'BUY', 'RUN_LOW20'),
                                     ('UV_VISOR_AMBER', 1, 'BUY', 'MIDZ1000_LOW')],
                     'short_exit': [('UV_VISOR_AMBER', 3, 'SELL', 'MIDZ2000_LOW')]},
 'UV_VISOR_RED': {'long_entry': [('UV_VISOR_AMBER', 3, 'SELL', 'ROLL500_LOW20')],
                  'long_exit': [('UV_VISOR_AMBER', 1, 'BUY', 'MIDZ500_LOW')],
                  'short_entry': [('UV_VISOR_RED', 2, 'SELL', 'RUN_HIGH20'),
                                  ('UV_VISOR_ORANGE', 2, 'SELL', 'MIDZ200_LOW')],
                  'short_exit': [('UV_VISOR_AMBER', 2, 'SELL', 'MIDZ2000_LOW')]},
 'UV_VISOR_YELLOW': {'long_entry': [('UV_VISOR_AMBER', 2, 'BUY', 'MIDZ500_LOW')],
                     'long_exit': [('UV_VISOR_ORANGE', 3, 'SELL', 'MIDZ1000_HIGH')],
                     'short_entry': [('UV_VISOR_ORANGE', 2, 'SELL', 'ROLL2000_HIGH20')],
                     'short_exit': [('UV_VISOR_MAGENTA', 2, 'SELL', 'MIDZ500_HIGH')]}}

    PRODUCT_TO_ID = {p: i for i, p in enumerate(PRODUCTS)}
    ID_TO_PRODUCT = {i: p for p, i in PRODUCT_TO_ID.items()}

    # Only quantities that appear in selected rules need to be scanned.
    WANTED_QTYS = {1, 2, 3, 4, 5}

    def bid(self):
        # Harmless outside Round 2; retained for compatibility with the official template.
        return 15

    # -----------------------------
    # traderData compression/state
    # -----------------------------

    def _fresh_state(self) -> Dict[str, Any]:
        return {
            "v": 2,
            "ts": -1,
            # mid2 history by compact product id string
            "m": {},
            # running low/high mid2 by compact product id string
            "lo": {},
            "hi": {},
            # compact book snapshots by product id string:
            # pid -> [[timestamp, bid, ask], ...]
            "bk": {},
            # target positions by compact product id string
            "tg": {},
            # seen public-trade keys in insertion order
            "seen": [],
        }

    def _load(self, raw: str) -> Dict[str, Any]:
        if not raw:
            return self._fresh_state()

        try:
            if raw.startswith("Z:"):
                payload = base64.b64decode(raw[2:].encode("ascii"))
                data = json.loads(zlib.decompress(payload).decode("utf-8"))
            else:
                # Backward-compatible JSON load. This helps when switching from an older
                # category-only trader to this combined trader in local tests.
                data = json.loads(raw)

            if not isinstance(data, dict) or data.get("v") != 2:
                return self._fresh_state()

            data.setdefault("ts", -1)
            data.setdefault("m", {})
            data.setdefault("lo", {})
            data.setdefault("hi", {})
            data.setdefault("bk", {})
            data.setdefault("tg", {})

            if "mh" in data:
                data["m"] = self._unpack_histories(data.pop("mh"))
            else:
                data.setdefault("m", {})

            if "se" in data:
                data["seen"] = self._unpack_seen(data.pop("se"))
            else:
                data.setdefault("seen", [])

            return data

        except Exception:
            return self._fresh_state()

    def _encode(self, data: Dict[str, Any]) -> str:
        # Normal trim first.
        self._trim_state(data, self.MAX_HIST, self.SNAP_KEEP, self.SEEN_KEEP)
        raw = self._encode_once(data)
        if len(raw) <= 48000:
            return raw

        # Emergency compression guard. It is better to temporarily lose some very-long
        # lookback precision than to have the platform truncate traderData and reset state.
        for hist_keep, snap_keep, seen_keep in [
            (2000, 8, 1800),
            (1800, 6, 1500),
            (1500, 5, 1200),
            (1200, 4, 900),
            (1000, 3, 600),
        ]:
            self._trim_state(data, hist_keep, snap_keep, seen_keep)
            raw = self._encode_once(data)
            if len(raw) <= 48000:
                return raw

        # Last-resort: keep strategy alive with short state.
        self._trim_state(data, 750, 2, 400)
        return self._encode_once(data)

    def _put_var(self, out: bytearray, n: int) -> None:
        n = int(n)
        while n >= 128:
            out.append((n & 127) | 128)
            n >>= 7
        out.append(n)

    def _read_var(self, raw: bytes, idx: int) -> Tuple[int, int]:
        shift = 0
        value = 0
        while idx < len(raw):
            b = raw[idx]
            idx += 1
            value |= (b & 127) << shift
            if b < 128:
                return value, idx
            shift += 7
        return 0, idx

    def _zigzag(self, n: int) -> int:
        return (int(n) << 1) ^ (int(n) >> 63)

    def _unzigzag(self, z: int) -> int:
        z = int(z)
        return (z >> 1) ^ -(z & 1)

    def _pack_int_list_into(self, out: bytearray, values: List[int]) -> None:
        self._put_var(out, len(values))
        prev = 0
        first = True
        for value in values:
            value = int(value)
            delta = value if first else value - prev
            first = False
            prev = value
            self._put_var(out, self._zigzag(delta))

    def _unpack_int_list_from(self, raw: bytes, idx: int) -> Tuple[List[int], int]:
        n, idx = self._read_var(raw, idx)
        values: List[int] = []
        prev = 0
        for j in range(n):
            z, idx = self._read_var(raw, idx)
            delta = self._unzigzag(z)
            value = delta if j == 0 else prev + delta
            values.append(int(value))
            prev = value
        return values, idx

    def _pack_histories(self, data: Dict[str, Any]) -> str:
        out = bytearray()
        items = []
        for pid, mids in data.get("m", {}).items():
            if mids:
                items.append((int(pid), mids))

        items.sort(key=lambda x: x[0])
        self._put_var(out, len(items))
        for pid, mids in items:
            self._put_var(out, pid)
            self._pack_int_list_into(out, mids)

        return base64.b64encode(zlib.compress(bytes(out), 6)).decode("ascii")

    def _unpack_histories(self, text: str) -> Dict[str, List[int]]:
        try:
            raw = zlib.decompress(base64.b64decode(text.encode("ascii")))
            idx = 0
            count, idx = self._read_var(raw, idx)
            out: Dict[str, List[int]] = {}
            for _ in range(count):
                pid, idx = self._read_var(raw, idx)
                values, idx = self._unpack_int_list_from(raw, idx)
                out[str(pid)] = values
            return out
        except Exception:
            return {}

    def _pack_seen(self, data: Dict[str, Any]) -> str:
        out = bytearray()
        seen = data.get("seen", [])
        self._put_var(out, len(seen))

        for key in seen:
            try:
                ts, pid, price, qty = key.split("|")
                self._put_var(out, int(ts))
                self._put_var(out, int(pid))
                self._put_var(out, int(price))
                self._put_var(out, int(qty))
            except Exception:
                # Skip malformed legacy keys.
                self._put_var(out, 0)
                self._put_var(out, 0)
                self._put_var(out, 0)
                self._put_var(out, 0)

        return base64.b64encode(zlib.compress(bytes(out), 6)).decode("ascii")

    def _unpack_seen(self, text: str) -> List[str]:
        try:
            raw = zlib.decompress(base64.b64decode(text.encode("ascii")))
            idx = 0
            count, idx = self._read_var(raw, idx)
            out: List[str] = []
            for _ in range(count):
                ts, idx = self._read_var(raw, idx)
                pid, idx = self._read_var(raw, idx)
                price, idx = self._read_var(raw, idx)
                qty, idx = self._read_var(raw, idx)
                out.append(f"{ts}|{pid}|{price}|{qty}")
            return out
        except Exception:
            return []

    def _for_save(self, data: Dict[str, Any]) -> Dict[str, Any]:
        save = {
            "v": 2,
            "ts": int(data.get("ts", -1)),
            "lo": data.get("lo", {}),
            "hi": data.get("hi", {}),
            "bk": data.get("bk", {}),
            "tg": data.get("tg", {}),
            "mh": self._pack_histories(data),
            "se": self._pack_seen(data),
        }
        return save

    def _encode_once(self, data: Dict[str, Any]) -> str:
        compact = json.dumps(self._for_save(data), separators=(",", ":"))
        payload = zlib.compress(compact.encode("utf-8"), 6)
        return "Z:" + base64.b64encode(payload).decode("ascii")

    def _trim_state(self, data: Dict[str, Any], hist_keep: int, snap_keep: int, seen_keep: int) -> None:
        m = data.get("m", {})
        for pid, mids in list(m.items()):
            if len(mids) > hist_keep:
                m[pid] = mids[-hist_keep:]

        bk = data.get("bk", {})
        for pid, snaps in list(bk.items()):
            if len(snaps) > snap_keep:
                bk[pid] = snaps[-snap_keep:]

        seen = data.get("seen", [])
        if len(seen) > seen_keep:
            data["seen"] = seen[-seen_keep:]

    # -----------------------------
    # book / history utilities
    # -----------------------------

    def _best_bid_ask(self, od: OrderDepth):
        if od is None or not od.buy_orders or not od.sell_orders:
            return None, None, 0, 0
        bid = max(od.buy_orders.keys())
        ask = min(od.sell_orders.keys())
        bid_vol = int(od.buy_orders[bid])
        ask_vol = int(-od.sell_orders[ask])
        return int(bid), int(ask), bid_vol, ask_vol

    def _pid(self, product: str) -> str:
        return str(self.PRODUCT_TO_ID[product])

    def _update_histories(self, state: TradingState, data: Dict[str, Any]) -> None:
        mids_by_pid = data.setdefault("m", {})
        lows = data.setdefault("lo", {})
        highs = data.setdefault("hi", {})

        for p in self.PRODUCTS:
            od = state.order_depths.get(p)
            bid, ask, _, _ = self._best_bid_ask(od)
            if bid is None:
                continue

            pid = self._pid(p)
            mid2 = int(bid + ask)

            mids = mids_by_pid.setdefault(pid, [])
            mids.append(mid2)
            if len(mids) > self.MAX_HIST:
                mids_by_pid[pid] = mids[-self.MAX_HIST:]
                mids = mids_by_pid[pid]

            lo = lows.get(pid)
            hi = highs.get(pid)
            if lo is None or mid2 < lo:
                lows[pid] = mid2
            if hi is None or mid2 > hi:
                highs[pid] = mid2

    def _save_current_books(self, state: TradingState, data: Dict[str, Any]) -> None:
        bk = data.setdefault("bk", {})

        for p in self.PRODUCTS:
            od = state.order_depths.get(p)
            bid, ask, _, _ = self._best_bid_ask(od)
            if bid is None:
                continue

            pid = self._pid(p)
            snaps = bk.setdefault(pid, [])
            snaps.append([int(state.timestamp), int(bid), int(ask)])
            if len(snaps) > self.SNAP_KEEP:
                bk[pid] = snaps[-self.SNAP_KEEP:]

    def _book_for_trade(self, product: str, trade_ts: int, state: TradingState, data: Dict[str, Any]) -> Dict[str, float]:
        pid = self._pid(product)
        snaps = data.get("bk", {}).get(pid, [])

        best = None
        for snap in snaps:
            try:
                ts = int(snap[0])
                if ts <= trade_ts and (best is None or ts > int(best[0])):
                    best = snap
            except Exception:
                continue

        if best is not None:
            bid = int(best[1])
            ask = int(best[2])
            return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2.0}

        # Fallback to current visible book. This is less exact, but prevents
        # losing early events before the first snapshot cache is populated.
        od = state.order_depths.get(product)
        bid, ask, _, _ = self._best_bid_ask(od)
        if bid is not None:
            return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2.0}

        return {}

    def _infer_side(self, price: int, book: Dict[str, float]) -> str:
        if not book:
            return "UNK"

        bid = book.get("bid")
        ask = book.get("ask")
        mid = book.get("mid")
        if bid is None or ask is None or mid is None:
            return "UNK"

        if price >= ask:
            return "BUY"
        if price <= bid:
            return "SELL"
        if price > mid:
            return "BUY"
        if price < mid:
            return "SELL"
        return "UNK"

    def _trade_timestamp(self, tr, fallback: int) -> int:
        ts = getattr(tr, "timestamp", None)
        try:
            return int(ts)
        except Exception:
            return int(fallback)

    # -----------------------------
    # state feature logic
    # -----------------------------

    def _mids(self, product: str, data: Dict[str, Any]) -> List[int]:
        return data.get("m", {}).get(self._pid(product), [])

    def _run_pos(self, product: str, data: Dict[str, Any]) -> float:
        pid = self._pid(product)
        mids = self._mids(product, data)
        if not mids:
            return math.nan

        lo = data.get("lo", {}).get(pid)
        hi = data.get("hi", {}).get(pid)
        if lo is None or hi is None or hi <= lo:
            return math.nan
        return (mids[-1] - lo) / (hi - lo)

    def _roll_pos(self, product: str, data: Dict[str, Any], window: int) -> float:
        mids = self._mids(product, data)
        min_history = max(20, window // 5)
        if len(mids) < min_history:
            return math.nan

        x = mids[-window:]
        lo = min(x)
        hi = max(x)
        if hi <= lo:
            return math.nan
        return (x[-1] - lo) / (hi - lo)

    def _mid_z(self, product: str, data: Dict[str, Any], window: int) -> float:
        mids = self._mids(product, data)
        min_history = max(20, window // 5)
        if len(mids) < min_history:
            return math.nan

        x = mids[-window:]
        n = len(x)
        mean = sum(x) / n
        var = sum((v - mean) * (v - mean) for v in x) / n
        if var <= 0:
            return math.nan
        return (x[-1] - mean) / math.sqrt(var)

    def _state_ok(self, product: str, state_name: str, data: Dict[str, Any]) -> bool:
        if state_name == "ANY":
            return True

        if state_name == "RUN_HIGH20":
            x = self._run_pos(product, data)
            return math.isfinite(x) and x >= 0.80

        if state_name == "RUN_LOW20":
            x = self._run_pos(product, data)
            return math.isfinite(x) and x <= 0.20

        if state_name.startswith("ROLL"):
            try:
                rest = state_name[4:]
                window_text, tail = rest.split("_", 1)
                x = self._roll_pos(product, data, int(window_text))
                if not math.isfinite(x):
                    return False
                if tail == "HIGH20":
                    return x >= 0.80
                if tail == "LOW20":
                    return x <= 0.20
            except Exception:
                return False

        if state_name.startswith("MIDZ"):
            try:
                rest = state_name[4:]
                window_text, side = rest.split("_", 1)
                z = self._mid_z(product, data, int(window_text))
                if not math.isfinite(z):
                    return False
                if side == "HIGH":
                    return z >= 1.0
                if side == "LOW":
                    return z <= -1.0
            except Exception:
                return False

        # No selected combined rules currently use SPREADZ. Return False instead
        # of storing another full spread history for 40 products.
        return False

    # -----------------------------
    # public-trade fingerprint logic
    # -----------------------------

    def _scan_new_events(self, state: TradingState, data: Dict[str, Any]) -> List[Tuple[str, int, str]]:
        seen_list = data.setdefault("seen", [])
        seen_set = set(seen_list)
        events: List[Tuple[str, int, str]] = []

        for p in self.PRODUCTS:
            trades = state.market_trades.get(p, [])
            if not trades:
                continue

            pid = self._pid(p)
            for tr in trades:
                price = getattr(tr, "price", None)
                qty = abs(getattr(tr, "quantity", 0))
                if price is None or qty not in self.WANTED_QTYS:
                    continue

                trade_ts = self._trade_timestamp(tr, state.timestamp)
                key = f"{trade_ts}|{pid}|{int(price)}|{int(qty)}"
                if key in seen_set:
                    continue

                seen_set.add(key)
                seen_list.append(key)

                book = self._book_for_trade(p, trade_ts, state, data)
                side = self._infer_side(int(price), book)
                if side != "UNK":
                    events.append((p, int(qty), side))

        if len(seen_list) > self.SEEN_KEEP:
            data["seen"] = seen_list[-self.SEEN_KEEP:]

        return events

    def _primitive_fired(
        self,
        primitive: Tuple[str, int, str, str],
        event_set: set,
        data: Dict[str, Any],
    ) -> bool:
        p, qty, side, state_name = primitive

        if (p, int(qty), side) not in event_set:
            return False

        return self._state_ok(p, state_name, data)

    def _signal_fired(
        self,
        primitives: List[Tuple[str, int, str, str]],
        event_set: set,
        data: Dict[str, Any],
    ) -> bool:
        if not primitives:
            return False

        for prim in primitives:
            if not self._primitive_fired(prim, event_set, data):
                return False
        return True

    # -----------------------------
    # execution
    # -----------------------------

    def _update_targets(self, event_set: set, data: Dict[str, Any]) -> None:
        targets = data.setdefault("tg", {})

        for target, cfg in self.RULES.items():
            pid = self._pid(target)
            current_target = int(targets.get(pid, 0))

            long_entry = self._signal_fired(cfg.get("long_entry", []), event_set, data)
            long_exit = self._signal_fired(cfg.get("long_exit", []), event_set, data)
            short_entry = self._signal_fired(cfg.get("short_entry", []), event_set, data)
            short_exit = self._signal_fired(cfg.get("short_exit", []), event_set, data)

            new_target = current_target

            if long_entry and short_entry:
                # Ambiguous packet. Do not churn position.
                new_target = current_target
            elif short_entry:
                new_target = -self.LIMIT
            elif long_entry:
                new_target = self.LIMIT
            elif long_exit and current_target > 0:
                new_target = 0
            elif short_exit and current_target < 0:
                new_target = 0

            targets[pid] = int(new_target)

    def _execute_targets(self, state: TradingState, data: Dict[str, Any]) -> Dict[str, List[Order]]:
        result: Dict[str, List[Order]] = {}
        targets = data.get("tg", {})

        for target in self.RULES.keys():
            od = state.order_depths.get(target)
            bid, ask, bid_vol, ask_vol = self._best_bid_ask(od)
            if bid is None:
                continue

            pos = int(state.position.get(target, 0))
            target_pos = int(targets.get(self._pid(target), 0))
            delta = target_pos - pos

            orders: List[Order] = []

            if delta > 0:
                qty = min(delta, self.LIMIT - pos, ask_vol)
                if qty > 0:
                    orders.append(Order(target, ask, qty))

            elif delta < 0:
                qty = min(-delta, pos + self.LIMIT, bid_vol)
                if qty > 0:
                    orders.append(Order(target, bid, -qty))

            if orders:
                result[target] = orders

        return result

    def run(self, state: TradingState):
        data = self._load(state.traderData)

        # Reset if a local backtest moves to a new/restarted stream.
        last_ts = int(data.get("ts", -1))
        if last_ts >= 0 and int(state.timestamp) < last_ts:
            data = self._fresh_state()

        self._update_histories(state, data)

        events = self._scan_new_events(state, data)
        if events:
            self._update_targets(set(events), data)

        result = self._execute_targets(state, data)

        self._save_current_books(state, data)
        data["ts"] = int(state.timestamp)

        if self.DEBUG and (events or result):
            readable_targets = {
                self.ID_TO_PRODUCT[int(pid)]: tgt
                for pid, tgt in data.get("tg", {}).items()
                if int(tgt) != 0
            }
            print(
                "ROUND5_COMBINED_EVENTS_V1",
                "ts=", state.timestamp,
                "events=", events,
                "targets=", readable_targets,
                "orders=", {p: [(o.symbol, o.price, o.quantity) for o in os] for p, os in result.items()},
            )

        return result, 0, self._encode(data)