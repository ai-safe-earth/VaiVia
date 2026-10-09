# Moved to the shared package (docs/route-design.md, decision 5). This
# re-export keeps pipeline imports working; R7 kept it because the emitter
# and its tests import from here.
from vaivia_routes.document import *
