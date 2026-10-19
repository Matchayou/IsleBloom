"""Common options for the single PostgreSQL controlled product composition."""


def add_service_options(parser):
    parser.add_argument('--blobs', default='.stage4/blobs')
    parser.add_argument('--private', default='.controlled/private')
    parser.add_argument('--catalog', help='Frozen model catalog JSON')
