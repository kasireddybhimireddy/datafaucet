import os

from datalabframework import logging
from datalabframework import elastic

from datalabframework.metadata.resource import get_metadata
from datalabframework._utils import ImmutableDict, to_ordered_dict

import pandas as pd
from datalabframework.spark import dataframe

from timeit import default_timer as timer

# purpose of engines
# abstract engine init, data read and data write
# and move this information to metadata

# it does not make the code fully engine agnostic though.

import pyspark

class Engine:
    def __init__(self, name, md, rootdir):
        self._name = name
        self._metadata = md
        self._rootdir = rootdir
        self._config = None
        self._env = None
        self._ctx = None
        self._type = None
        self._version = None
        self._timezone = md.get('engine', {}).get('timezone')
        self._timestamps = md.get('engine', {}).get('timestamps')

    def config(self):
        keys = [
            'type',
            'name',
            'version',
            'config',
            'env',
            'rootdir',
            'timezone'
        ]
        d = {
            'type': self._type,
            'name': self._name,
            'version': self._version,
            'config': self._config,
            'env': self._env,
            'rootdir': self._rootdir,
            'timezone': self._timezone,
            'timestamps': self._timestamps
        }
        return ImmutableDict(to_ordered_dict(d,keys))

    def context(self):
        return self._ctx

    def load(self, path=None, provider=None, **kargs):
        raise NotImplementedError

    def save(self, obj, path=None, provider=None, **kargs):
        raise NotImplementedError
    
    def copy(self, md_src, md_trg, mode='append'):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

class NoEngine(Engine):
    def __init__(self):
        self._type = 'none'
        self._version = 0

        super().__init__('no-compute-engine', {}, os.getcwd())

    def load(self, path=None, provider=None, **kargs):
        raise ValueError('No engine loaded.')

    def save(self, obj, path=None, provider=None, **kargs):
        raise ValueError('No engine loaded.')

    def copy(self, md_src, md_trg, mode='append'):
        raise ValueError('No engine loaded.')

    def stop(self):
        pass

class SparkEngine(Engine):
    def set_submit_args(self):

        submit_args = ''
        submit_md = self._metadata.get('engine', {}).get('submit', {})

        #### submit: jars
        items = submit_md.get('jars')
        jars = items if items else []

        if jars:
            submit_jars = ' '.join(jars)
            submit_args = '{} --jars {}'.format(submit_args, submit_jars)

        #### submit: packages
        items = submit_md.get('packages')
        packages = items if items else []

        for v in self._metadata.get('providers', {}).values():
            if v['service'] == 'mysql':
                packages.append('mysql:mysql-connector-java:8.0.12')
            elif v['service'] == 'sqlite':
                packages.append('org.xerial:sqlite-jdbc:jar:3.25.2')
            elif v['service'] == 'postgres':
                packages.append('org.postgresql:postgresql:42.2.5')
            elif v['service'] == 'mssql':
                packages.append('com.microsoft.sqlserver:mssql-jdbc:6.4.0.jre8')

        if packages:
            submit_packages = ','.join(packages)
            submit_args = '{} --packages {}'.format(submit_args, submit_packages)

        #### submit: py-files
        items = submit_md.get('py-files')
        pyfiles = items if items else []

        if pyfiles:
            submit_pyfiles = ','.join(pyfiles)
            submit_args = '{} --py-files {}'.format(submit_args, submit_pyfiles)

        # set PYSPARK_SUBMIT_ARGS env variable
        submit_args = '{} pyspark-shell'.format(submit_args)
        os.environ['PYSPARK_SUBMIT_ARGS'] = submit_args

    def set_context_args(self, conf):
        # jobname
        app_name = self._metadata.get('engine', {}).get('jobname', self._name)
        conf.setAppName(app_name)

        # set master
        conf.setMaster(self._metadata.get('engine', {}).get('master', 'local[*]'))

    def set_conf_kv(self, conf):
        conf_md = self._metadata.get('engine', {}).get('config', {})

        # setting for minio
        for v in self._metadata.get('providers', {}).values():
            if v['service'] == 'minio':
                conf.set("spark.hadoop.fs.s3a.endpoint", 'http://{}:{}'.format(v['hostname'], v.get('port', 9000))) \
                    .set("spark.hadoop.fs.s3a.access.key", v['access']) \
                    .set("spark.hadoop.fs.s3a.secret.key", v['secret']) \
                    .set("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem") \
                    .set("spark.hadoop.fs.s3a.path.style.access", True)
                break

        # if timezone is not set, engine treats timestamps as 'naive' 
        if self._timestamps == 'naive':
            conf.set('spark.sql.session.timeZone','UTC')
            conf.set('spark.driver.extraJavaOptions','-Duser.timezone=UTC')
            conf.set('spark.executor.extraJavaOptions','-Duser.timezone=UTC')
        elif self._timezone:
            timezone = self._timezone
            conf.set('spark.sql.session.timeZone',timezone)
            conf.set('spark.driver.extraJavaOptions',f'-Duser.timezone={timezone}')
            conf.set('spark.executor.extraJavaOptions',f'-Duser.timezone={timezone}')
        else:
            # use spark and system defaults
            pass
            
        for k,v in conf_md.items():
            if isinstance(v, (bool, int, float, str)):
                conf.set(k,v)

    def __init__(self, name, md, rootdir):
        super().__init__(name, md, rootdir)

        # set submit args via env variable
        self.set_submit_args()

        # set spark conf object
        conf = pyspark.SparkConf()
        self.set_context_args(conf)
        self.set_conf_kv(conf)

        # stop current session before creating a new one
        pyspark.SparkContext.getOrCreate().stop()

        # set log level fro spark
        sc = pyspark.SparkContext(conf=conf)

        # pyspark set log level method
        # (this will not suppress WARN before starting the context)
        sc.setLogLevel("ERROR")

        # record the data in the engine object for debug and future references
        self._config = dict(sc._conf.getAll())
        self._env = {'PYSPARK_SUBMIT_ARGS': os.environ['PYSPARK_SUBMIT_ARGS']}

        self._type = 'spark'
        self._version = sc.version

        # store the sql context
        self._ctx = pyspark.SQLContext(sc)

    def stop(self):
        pyspark.SparkContext.getOrCreate().stop()

        
    def load(self, path=None, provider=None, catch_exception=True, **kargs):
        if isinstance(path, ImmutableDict):
            md = path.to_dict()
        elif isinstance(path, str):
            md = get_metadata(self._rootdir, self._metadata, path, provider)
        elif isinstance(path, dict):
            md = path

        core_start = timer()
        obj = self.load_dataframe(md, catch_exception, **kargs)
        core_end = timer()
        if obj is None:
            return obj
            
        prep_start = timer()
        date_column = '_date' if md['date_partition'] else md['date_column']
        obj = dataframe.filter_by_date(
                obj,
                date_column,
                md['date_start'],
                md['date_end'],
                md['date_window'])

        obj = dataframe.cache(obj, md['cache'])

        num_rows = obj.count()
        num_cols = len(obj.columns)
        prep_end = timer()

        logging.info({
            'md': dict(md), 
            'mode': kargs.get('mode', md.get('options', {}).get('mode')),
            'result': 'success', 
            'records': num_rows, 
            'columns': num_cols, 
            'time': prep_end - core_start, 
            'time_core': core_end-core_start, 
            'time_prep': prep_end - prep_start
        })
        
        return obj

    def load_dataframe(self, md, catch_exception=True, **kargs):
        obj = None
        options = md['options']

        try:
            if md['service'] in ['local', 'file']:
                if md['format'] == 'csv':
                    try:
                        obj = self._ctx.read.options(**options).csv(md['url'], **kargs)
                    except:
                        obj = self._ctx.createDataFrame(pd.read_csv(md['url'], **kargs))

                if md['format'] == 'json':
                    try:
                        obj = self._ctx.read.options(**options).json(md['url'], **kargs)
                    except:
                        obj = self._ctx.createDataFrame(pd.read_json(md['url'], **kargs))
                if md['format'] == 'jsonl':
                    try:
                        obj = self._ctx.read.option('multiLine', True).options(**options).json(md['url'], **kargs)
                    except:
                        obj = self._ctx.createDataFrame(pd.read_json(md['url'], lines=True, **kargs))
                elif md['format'] == 'parquet':
                    try:
                        obj = self._ctx.read.options(**options).parquet(md['url'], **kargs)
                    except:
                        obj = self._ctx.createDataFrame(pd.read_parquet(md['url'], **kargs))

            elif md['service'] in ['hdfs', 's3', 'minio']:
                if md['format'] == 'csv':
                    obj = self._ctx.read.options(**options).csv(md['url'], **kargs)
                if md['format'] == 'json':
                    obj = self._ctx.read.options(**options).json(md['url'], **kargs)
                if md['format'] == 'jsonl':
                    obj = self._ctx.read.option('multiLine', True).options(**options).json(md['url'], **kargs)
                elif md['format'] == 'parquet':
                    obj = self._ctx.read.options(**options).parquet(md['url'], **kargs)

            elif md['service'] in ['sqlite', 'mysql', 'postgres', 'mssql', 'oracle']:

                obj = self._ctx.read \
                    .format('jdbc') \
                    .option('url', md['url']) \
                    .option("dbtable", md['resource_path']) \
                    .option("driver", md['driver']) \
                    .option("user", md['username']) \
                    .option('password', md['password']) \
                    .options(**options)

                #load the data from jdbc
                obj = obj.load(**kargs)

            elif md['service'] == 'elastic':
                results = elastic.read(md['url'], options.get('query', {}))
                rows = [pyspark.sql.Row(**r) for r in results]
                obj =  self.context().createDataFrame(rows)
            else:
                raise ValueError(f'Unknown service "{md["service"]}"')
        except Exception as e:
            if catch_exception:
                logging.error('could not load')
                print(e)
                return None
            else:
                raise e
        
        return obj

    def save(self, obj, path=None, provider=None, catch_exception=True, **kargs):

        if isinstance(path, ImmutableDict):
            md = path.to_dict()
        elif isinstance(path, str):
            md = get_metadata(self._rootdir, self._metadata, path, provider)
        elif isinstance(path, dict):
            md = path

        prep_start = timer()
        if md['date_partition'] and md['date_column']:
            tzone = 'UTC' if self._timestamps=='naive' else self._timezone
            obj = dataframe.add_datetime_columns(obj, column=md['date_column'], tzone=tzone)
            kargs['partitionBy'] = ['_date'] + kargs.get('partitionBy', md.get('options', {}).get('partitionBy', []))
        
        if md['update_column']:
            obj = dataframe.add_update_column(obj, tzone=self._timezone)

        if md['hash_column']:
            obj = dataframe.add_hash_column(obj, cols=md['hash_column'])

        date_column = '_date' if md['date_partition'] else md['date_column']
        obj = dataframe.filter_by_date(
                obj,
                date_column,
                md['date_start'],
                md['date_end'],
                md['date_window'])

        obj = dataframe.cache(obj, md['cache'])
        obj = obj.repartition(1,*kargs['partitionBy']) if kargs.get('partitionBy') else obj.coalesce(1)

        num_rows = obj.count()
        num_cols = len(obj.columns)
        prep_end = timer()
                                 
        core_start = timer()
        self.save_dataframe(obj, md, catch_exception, **kargs)
        core_end = timer()
                                 
        logging.info({
            'md': dict(md), 
            'mode': kargs.get('mode', md.get('options', {}).get('mode')),
            'result': 'success', 
            'records': num_rows, 
            'columns': num_cols, 
            'time':core_end - prep_start,
            'time_core':core_end-core_start, 
            'time_prep': prep_end - prep_start
        })
                                 
                                 
    def save_dataframe(self, obj, md, catch_exception=True, **kargs):

        options = md.get('options', {})
                                 
        try:
            if md['service'] in ['local', 'file']:
                if md['format'] == 'csv':
                    obj.toPandas().to_csv(md['url'], **kargs)
                if md['format'] == 'json':
                    obj.toPandas().to_json(md['url'], **kargs)
                if md['format'] == 'jsonl':
                    obj.toPandas().to_json(md['url'], lines=True, **kargs)
                elif md['format'] == 'parquet':
                    obj.toPandas().to_parquet(md['url'], **kargs)

            elif md['service'] in ['hdfs', 's3', 'minio']:
                if md['format'] == 'csv':
                    obj.write.options(**options).csv(md['url'], **kargs)
                if md['format'] == 'json':
                    obj.write.options(**options).option('multiLine', True).json(md['url'], **kargs)
                if md['format'] == 'jsonl':
                    obj.write.options(**options).json(md['url'], **kargs)
                elif md['format'] == 'parquet':
                    obj.write.options(**options).parquet(md['url'], **kargs)
                else:
                    logging.info('format unknown')

            elif md['service'] in ['sqlite', 'mysql', 'postgres', 'oracle']:
                obj.write \
                    .format('jdbc') \
                    .option('url', md['url']) \
                    .option("dbtable", md['resource_path']) \
                    .option("driver", md['driver']) \
                    .option("user", md['username']) \
                    .option('password', md['password']) \
                    .options(**options) \
                    .save(**kargs)

            elif md['service'] == 'elastic':
                mode = kargs.get("mode", None)
                obj = [row.asDict() for row in obj.collect()]
                elastic.write(obj, md['url'], mode, md['resource_path'], options['settings'], options['mappings'])
            else:
                raise ValueError('don\'t know how to handle this')
        except Exception as e:
            if catch_exception:
                logging.error({'md': md, 'result': 'error'})
                print(e)
            else:
                raise e

    def copy(self, md_src, md_trg, mode='append'):
        # timer
        timer_start = timer()
                                 
        # src dataframe
        df_src = self.load(md_src)
                                 
        if df_src is None:
            return
                                 
        if mode=='overwrite':
            self.save(df_src, md_trg, mode=mode)
            num_rows = df_src.count()
            num_cols = len(df_src.columns)

            logging.notice({
                'mode': mode, 
                'records': num_rows, 
                'columns': num_cols,
                'time': timer() - timer_start
            })
            return

        # trg dataframe (if exists)
        try:
            df_trg = self.load(md_trg, catch_exception=False)
        except:
            df_trg = dataframe.empty(df_src)
                                 
        # de-dup (exclude the _updated column)
        df = dataframe.diff(df_src,df_trg,['_date', '_datetime', '_updated', '_hash'])
                                 
        num_rows = df.count()
        num_cols = len(df.columns)
                                 
        # save diff
        if num_rows:
            self.save(df, md_trg, mode=mode)
        
        logging.notice({
            'mode': mode, 
            'records': num_rows, 
            'columns': num_cols,
            'time': timer() - timer_start
        })
            
                                 
def get(name, md, rootdir):
    engine = NoEngine()

    if md.get('engine', {}).get('type') == 'spark':
         engine = SparkEngine(name, md, rootdir)

    # if md.get('engine', {}).get('type') == 'pandas':
    #      engine = PandasEngine(name, md, rootdir)

    return engine
