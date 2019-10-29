import functools

from pyspark.sql import DataFrame

from datafaucet import logging
import pyspark
import pyspark.sql.functions as F
import pyspark.sql.types as T

from datafaucet.spark import dataframe
from datafaucet.spark import aggregations as A
from datafaucet.spark import utils
from datafaucet.spark import functions

def to_dict(d=None):
    m = d or {}
    if isinstance(d, dict):
        m = m
    elif isinstance(d, (list, tuple)):
        m = {}
        for e in d:
            if isinstance(e, (list, tuple)):
                if len(e)>1:
                    m[e[0]]=e[1]
                elif len(e)>0:
                    m[e[0]] = e[0]
            elif isinstance(e, str):
                m[e]=e
    elif isinstance(d, str):
        m = dict(d, d)
    else:
        m = {}
    return m

class Cols:
    def __init__(self, df, scols=None, gcols=None):
        self.df = df
        self.gcols = gcols or []

        self.scols = scols or df.columns
        self.scols = list(set(self.scols) - set(self.gcols))

    @property
    def columns(self):
        return [x for x in self.df.columns if x in (self.scols + self.gcols)]

    def _getcols(self, *colnames):
        for c in set(colnames) - set(self.df.columns):
            logging.warning(f'Column not found: {c}')

        return list(set(colnames) & set(self.df.columns))

    def get(self, *colnames, sort=None):
        self.scols = self._getcols(*colnames)
        return self

    def find(self, *by_regex, by_type=None, by_func=None, sort=None):
        self.scols = dataframe.columns(self.df,*by_regex, by_type=by_type, by_func=by_func)
        return self

    def create(self, *colnames, t='string'):
        df = self.df

        for c in colnames:
            df = df.withColumn(c, F.lit(None).cast(t))

        self.scols = self._getcols(*colnames)
        return self

    def groupby(self, *colnames):
        #set group by list
        self.gcols = self._getcols(*colnames)

        #update select list
        scols = list(set(self.scols) - set(self.gcols))
        self.scols = self._getcols(*scols)
        return self

    def rename(self, mapping=None, prefix='', postfix=''):
        if isinstance(mapping, str):
            if len(self.scols)==1:
                d = {self.scols[0]:mapping}
            else:
                m = [f'mapping_{c}' for c in range(len(self.scols))]
                d = dict(zip(self.scols, m))
        else:
            d = to_dict(mapping)

        d = d or dict(zip(self.scols, self.scols))

        mapped_cols = set(self.scols) & set(d.keys())

        df = self.df

        for c in mapped_cols:
            df = df.withColumnRenamed(c, prefix+d[c]+postfix)

        return df

    def order(self, *cols, where='start'):

        ordered = [x for x in cols if x in self.scols]
        other = [x for x in self.scols if x not in cols]

        return self.df.select(*ordered, *other)

    @property
    def rows(self):
        from datafaucet.spark.rows import Rows
        return Rows(self.df, self.scols, self.gcols)

    @property
    def data(self):
        from datafaucet.spark.data import Data
        return Data(self.df, self.scols, self.gcols)

    ### actions
    def drop(self):
        df = self.df
        for c in self.scols:
            df = df.drop(c)

        return df

    def apply(self, f, prefix='', postfix=''):
        input_cols = self.scols
        output_cols = [f'{prefix}{c}{postfix}' for c in input_cols]

        df = self.df

        for ci, co in zip(input_cols, output_cols):
            df = df.withColumn(co, f(F.col(ci)))

        return df

    def expand(self, n, sep='_'):
        df = self.df
        for ci in self.scols:
            df = functions.expand(df, ci, n, sep)
        return df

    def randint(self, min=0, max=1, seed=None):
        df = self.df
        for ci in self.scols:
            df = functions.randint(df, ci, min, max, seed)
        return df

    def randn(self, mu=0.0, sigma=1.0, seed=None):
        df = self.df
        for ci in self.scols:
            df = functions.randn(df, ci, mu, sigma, seed)
        return df

    def rand(self, min=0.0, max=1.0, seed=None):
        df = self.df
        for ci in self.scols:
            df = functions.rand(df, ci, min, max, seed)
        return df

    def fake(self, generator):
        return self.apply(utils.fake(generator))

    def hash(self, method='hash', preserve_type=True):
        f = {
            'crc32': F.crc32,
            'hash': F.hash
        }.get(method, F.hash)

        df = self.df
        cast = lambda ci: ci
        for c in self.scols:
            t = df.schema[c].dataType

            # keep the hash but preserve original type,
            # at the cost of more hash clashes (reduced hash space)

            #cast to string if method is crc32
            col = F.col(c).cast('string') if method=='crc32' else F.col(c)

            if preserve_type and isinstance(t, (T.NumericType,  T.StringType)):
                cast = lambda ci: ci.cast(t)

            df = df.withColumn(c, cast(f(col)))

        return df

    def hashstr(self, method='crc32', salt=''):
        f = {
            'crc32': F.crc32,
            'md5': F.md5,
            'md5-8': F.md5,
            'md5-4': F.md5,
            'sha1': F.sha1
        }.get(method, F.crc32)

        df = self.df
        for c in self.scols:
            col = F.col(c).cast('string')
            h = f(F.concat(col, F.lit(salt)))

            if method=='crc32':
                res = F.conv(h.cast('string'), 10, 16)
            elif method=='md5-8':
                res = F.substring(h, 0, 16)
            elif method=='md5-4':
                res = F.substring(h, 0, 8)
            else:
                res = h

            df = df.withColumn(c, res)

        return df


    def obscure(self, password=None):
        df = self.df
        for c in self.scols:
            df = df.withColumn(c, F.col(c))
        return df

    def unobscure(self, password=None):
        df = self.df
        for c in self.scols:
            df = df.withColumn(c, F.col(c))
        return df

    def lower(self):
        return self.apply(F.lower)

    def upper(self):
        return self.apply(F.upper)

    def translate(self, matching=[], replace=[]):
        func = functools.partial(F.translate, matching, replace)
        return self.apply(func)

    def split(self, pattern):
        func = functools.partial(F.split, pattern=pattern)
        return self.apply(func)

    def unidecode(self, pre='', post=''):
        return self.apply(utils.unidecode, pre=pre, post=post)

    def one(self, as_type='pandas'):
        return self.collect(1, as_type=as_type)

    def collect(self, n, as_type='pandas'):
        return self.df.select(self.scols).limit(n).toPandas().T

    def summary(self):
        return utils.summary(self, self._cols)

    def agg(self, *args):

        funcs = {}
        for arg in args:
            if isinstance(arg, (list, tuple)):
                for e in arg:
                    funcs[str(e)] = e
            elif isinstance(arg, dict):
                funcs.update(arg)
            elif isinstance(arg, str):
                funcs[arg] = arg
            else:
                funcs[str(arg)] = arg

        for k,v in funcs.items():
            if k in A.all:
                funcs[k] = A.all[k]

        df = self.df

        aggs = []
        def grouped(c):
            g = df.select(c, *self.gcols)
            return g if not self.gcols else g.groupby(*self.gcols)

        for c in self.scols:

            groupeddata_funcs = []
            dataframe_funcs = []

            agg_gdf = []
            for n,f in funcs.items():
                if not isinstance(f, A.df_functions):
                    groupeddata_funcs.append(f(F.col(c)).alias(n))

            if groupeddata_funcs:
                agg_gdf = [grouped(c).agg(F.lit(c).alias('colname'), *groupeddata_funcs)]

            agg_dff = []
            for n,f in funcs.items():
                if isinstance(f, A.df_functions):
                    agg = f(df, c, by=self.gcols).withColumnRenamed('result', n)
                    agg_dff.append(agg)

            join_cols = self.gcols + ['colname']
            dfs = agg_gdf + agg_dff

            agg = functools.reduce( lambda a, b: a.join(b, on=join_cols, how='outer'), dfs)
            aggs.append(agg)

        return functools.reduce( lambda a, b: a.union(b), aggs)

    def featurize(self, funcs):
        d = self.agg(funcs)
        all = [F.first(c).alias(f'{c}') for c in set(d.columns) - {*self.gcols, 'colname'}]
        r = d.groupby(*self.gcols).pivot('colname').agg(*all)
        return r


def _cols(self):
    return Cols(self)

DataFrame.cols = property(_cols)
