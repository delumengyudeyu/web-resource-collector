"""Select a disposable database before importing any application module."""
import os
import tempfile

os.environ['COLLECTOR_HOME']=tempfile.mkdtemp(prefix='webcollector-suite-')
os.environ['COLLECTOR_ALLOW_PRIVATE']='1'
