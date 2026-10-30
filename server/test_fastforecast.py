"""Ті самі тести трекера/журналу з numpy-методами (fastforecast.install()), як у rt_server."""
import unittest
import fastforecast

fastforecast.install()
from test_tracker import *      # noqa: E402,F401,F403
from test_outcomes import *     # noqa: E402,F401,F403

if __name__ == "__main__":
    unittest.main()
