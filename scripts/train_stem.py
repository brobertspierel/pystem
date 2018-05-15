# -*- coding: utf-8 -*-
"""
Train and write to disk a spatiotemporal exploratory model
 

@author: shooper
"""
import os
import sys
import time
import shutil
import warnings
import sqlite3
import sqlalchemy
from datetime import datetime
from osgeo import gdal, ogr
from sklearn.externals.joblib import Parallel, delayed
import pandas as pd
import numpy as np
import cPickle as pickle
from multiprocessing import Pool

import stem

def _par_train_stem(n_jobs, n_sets, df_train, predict_cols, target_col, min_obs, df_sets, model_func, model_type, max_features, dt_path_template, db_path, max_val):
    
    """ private helper function to avoid exec free variable error"""
    start_time = time.time()
    s = Parallel(n_jobs, backend="threading")(
            delayed(stem.par_train_estimator)(
                i, n_sets, start_time, df_train, predict_cols, target_col,
                min_obs, set_info, model_func, model_type, max_features,
                dt_path_template, db_path, max_val) 
                for i, (set_id, set_info) in enumerate(df_sets.iterrows()))
    return s
    

def main(params, pct_train=None, min_oob=0, gsrd_shp=None, resolution=30, make_oob_map=False, snap_coord=None, oob_map_metric='oob_rate', n_jobs=1, oob_drop=None):
    t0 = time.time()
    
    inputs = stem.read_params(params)
    
    # Convert params to named variables and check for required vars
    for i in inputs:
        exec ("{0} = str({1})").format(i, inputs[i])
    try:
        if 'max_features' not in locals(): max_features=None
        if 'min_oob' in inputs: min_oob = int(min_oob)
        num_vars = stem.vars_to_numbers(cell_size, support_size, sets_per_cell,
                                   min_obs, max_features, pct_train)
        cell_size, support_size, sets_per_cell, min_obs, max_features, pct_train = num_vars
        str_check = sample_txt, target_col, mosaic_path, out_dir, model_type
    except NameError as e:
        missing_var = str(e).split("'")[1]
        msg = "Variable '%s' not specified in param file:\n%s" % (missing_var, params)
        raise NameError(msg)
    df_var = pd.read_csv(var_info, sep='\t', index_col='var_name')

    # Read in training samples and check that df_train has exactly the same
    #   columns as variables specified in df_vars    
    df_train = pd.read_csv(sample_txt, sep='\t')
    n_samples = len(df_train)
    unmatched_vars = [v for v in df_var.index if v not in [c for c  in df_train]]
    if len(unmatched_vars) != 0:
        unmatched_str = '\n\t'.join(unmatched_vars)
        msg = 'Columns not in sample_txt but specified in params:\n\t' + unmatched_str
        import pdb; pdb.set_trace()
        raise NameError(msg)
    if target_col not in df_train.columns:
        raise NameError('target_col "%s" not in sample_txt: %s' % (target_col, sample_txt))
    if 'max_target_val' in inputs:
        max_target_val = int(max_target_val)
    else:
        max_target_val = df_train[target_col].max()
    
    # Make a timestamped output directory if outdir not specified
    now = datetime.now()
    date_str = str(now.date()).replace('-','')
    time_str = str(now.time()).replace(':','')[:4]
    if not 'out_dirname' in locals(): out_dirname = target_col
    stamp = '{0}_{1}_{2}'.format(out_dirname, date_str, time_str)
    out_dir = os.path.join(out_dir, stamp)
    os.makedirs(out_dir) # With a timestamp in dir, no need to check if it already exists
    shutil.copy2(params, out_dir) #Copy the params for reference '''

    predict_cols = sorted(np.unique([c for c in df_train.columns for v in df_var.index if v in c]))
    df_var = df_var.reindex(df_var.index.sort_values())# Make sure predict_cols and df_var are in the same order
    
    # If there are variables that should remain constant across the modeling
    #   region, get the names
    if 'constant_vars' in locals():
        constant_vars = sorted([i.strip() for i in constant_vars.split(',')])
        predict_cols += constant_vars
    
    # Get samples and support set bounds
    if 'gsrd_shp' not in locals(): gsrd_shp = None
    if snap_coord:
        snap_coord = [int(c) for c in snap_coord.split(',')]
    out_txt = os.path.join(out_dir, stamp + '.txt')
    df_sets = stem.get_gsrd(mosaic_path, cell_size, support_size,
                            sets_per_cell, df_train, min_obs,
                            target_col, predict_cols, out_txt,
                            gsrd_shp, pct_train, snap_coord=snap_coord)
    n_sets = len(df_sets)            
    
    # Create SQL DB and add train sample table
    print 'Dumping train_txt to database...'
    t1 = time.time()
    db_path = os.path.join(out_dir, stamp + '.db')
    engine = sqlalchemy.create_engine('sqlite:///%s' % db_path)
    #df_train.to_sql('train_sample', engine, chunksize=10000)
    print '%.1f minutes\n' % ((time.time() - t1)/60)
    
    # Train a tree for each support set
    t1 = time.time()
    if model_type.lower() == 'classifier':
        print 'Training STEM with classifier algorithm...'
        model_func = stem.fit_tree_classifier
    else:
        print 'Training STEM with regressor algorithm...'
        model_func = stem.fit_tree_regressor
    x_train = df_train.reindex(columns=predict_cols)
    y_train = df_train[target_col]
    importance_cols = ['importance_%s' % c for c in predict_cols]
    for c in importance_cols:
        df_sets[c] = 0
    
    # Train estimators
    dropped_sets = pd.DataFrame(columns=df_sets.columns)
    dt_dir = os.path.join(out_dir, 'decisiontree_models')
    if not os.path.exists(dt_dir):
        os.mkdir(dt_dir)
    dt_path_template = os.path.join(dt_dir, stamp + '_decisiontree_%s.pkl')
    
    #oob_rates = [0]
    n_jobs = int(n_jobs)

    sets = _par_train_stem(n_jobs, n_sets, df_train, predict_cols, target_col,
                           min_obs, df_sets, model_func, model_type, max_features,
                           dt_path_template, db_path, max_target_val)
    support_sets, samples = zip(*sets)
    df_sets = pd.DataFrame(list(support_sets))\
                .dropna(subset=['dt_file'])\
                .rename_axis('set_id')
    df_sets.to_csv(os.path.join(out_dir, 'support_sets.txt'), sep='\t')
    
    # Consider moving this back to train function by switching to DBMS with multithread support
    print '\n\nMaking relationship table for samples and sets...'
    t1 = time.time()
    set_samples = pd.concat(list(samples), ignore_index=True)
    set_samples.to_sql('set_samples', engine, chunksize=100000)
    print '%.1f minutes\n' % ((time.time() - t1)/60)
    
    # Calculate OOB rates and drop sets with too low OOB
    print 'Calculating OOB rates and dropping sets with high OOB error...'
    t1 = time.time()
    try:
        df_sets, low_oob, oob_metric = stem.get_oob_rates(df_sets, df_train, db_path, target_col, predict_cols, min_oob, model_type, drop_expression=oob_drop)
    except Exception as e:
        import pdb; pdb.set_trace()
    if oob_drop and len(low_oob) > 0:
        df_sets.drop(low_oob.index, inplace=True)
        low_oob_shp = os.path.join(out_dir, 'low_oob_sets.shp')
        low_oob.drop('dt_model', axis=1, inplace=True)
        stem.coords_to_shp(low_oob, gsrd_shp, low_oob_shp)
    set_shp = os.path.join(out_dir, 'support_sets.shp')
    try:
        stem.coords_to_shp(df_sets.drop('dt_model', axis=1), gsrd_shp, set_shp)
    except Exception as e:
        import pdb; pdb.set_trace()
        print e.message
    print 'Min OOB rate after dropping: ', df_sets[oob_metric].min()
    print 'Estimated average OOB score: ', int(df_sets[oob_metric].mean())
    print '%.1f minutes\n' % ((time.time() - t1)/60)

    # Write df_sets and each decison tree to disk
    print 'Saving support set info...'
    #set_txt = os.path.join(dt_dir, stamp + '_support_sets.txt')
    df_sets['set_id'] = df_sets.index
    #df_sets = df_sets.drop('dt_model', axis=1)#.to_csv(set_txt, sep='\t', index=False)
    df_sets.drop('dt_model', axis=1).to_sql('support_sets', engine)
    t1 = time.time()
    print '%.1f minutes\n' % ((time.time() - t1)/60) #"""
    
    '''stamp = os.path.basename(out_dir)
    db_path = os.path.join(out_dir, stamp + '.db')
    engine = sqlalchemy.create_engine('sqlite:///%s' % db_path)
    with engine.connect() as con, con.begin():
        df_sets = pd.read_sql_table('support_sets', con, index_col='set_id')
    predict_cols = ['aspectNESW','aspectNWSE','brightness','delta_brightness','delta_greenness','delta_nbr','delta_wetness', 'elevation','greenness','mse','nbr','slope','time_since','wetness']#'''
    if make_oob_map or oob_map_metric in inputs:
        # Check if oob_map params were specified. If not, set to defaults
        if 'n_tiles' not in inputs:
            n_tiles = 40, 90
            print 'n_tiles not specified. Using default: %s x %s ...\n' % (n_tiles)
            
        else:
            n_tiles = int(n_tiles[0]), int(n_tiles[1])
            
        print 'Calculating OOB score and making OOB score map...'
        try:
            ds = gdal.Open(mosaic_path)
            ar = ds.ReadAsArray()
            mask = ar != 0
            del ar
            xsize = ds.RasterXSize
            ysize = ds.RasterYSize
            tx = ds.GetGeoTransform()
            prj = ds.GetProjection()
            driver = ds.GetDriver()
            ds = None  
        except:
            mosaic_ds = ogr.Open(mosaic_path)
            if 'resolution' not in inputs:
                warnings.warn('Resolution not specified. Assuming default of 30...\n')
            mask = mosaic_ds.GetLayer()
            min_x, max_x, min_y, max_y = mask.GetExtent()
            ul_x = min_x - ((min_x - snap_coord[0]) % resolution)
            ul_y = max_y - ((max_y - snap_coord[1]) % resolution)
            xsize = int((max_x - ul_x)/resolution)
            ysize = int((ul_y - min_y)/resolution)
            prj = mask.GetSpatialRef().ExportToWkt()
            driver = gdal.GetDriverByName('gtiff')
            x_res = resolution
            y_res = -resolution
            tx = ul_x, x_res, 0, ul_y, 0, y_res
        
        avg_dict, df_sets = stem.oob_map(ysize, xsize, 0, mask, n_tiles, tx,
                                     support_size, db_path, df_sets, df_train, target_col,
                                     predict_cols, out_dir,
                                     stamp, prj, driver, oob_map_metric)
        df_sets.to_csv(set_txt, sep='\t')#'''

        avg_oob = round(avg_dict[oob_map_metric], 1)
        avg_cnt = int(round(avg_dict['count'], 0))
        
        print '\nAverage OOB score: .................... %.1f' % avg_oob
        print '\nAverage number of overlapping sets: ... %s\n' % avg_cnt
        
        print 'Time to make OOB score map: %.1f hours\n' % ((time.time() - t1)/3600)
    
    # Record params in inventory text file
    if 'inventory_txt' in inputs:
        t1 = time.time()
        print 'Getting model info...\n'
        df_inv = pd.read_csv(inventory_txt, sep='\t', index_col='stamp')
        n_sets = len(df_sets)
        '''if 'sample' in sample_txt:
            n_samples = int(sample_txt.split('_')[1].replace('sample',''))
        inv_columns = df_inv.columns
        if 'n_sets' in inv_columns: df_inv.ix[stamp, 'n_sets'] = n_sets
        if 'n_samples' in inv_columns: df_inv.ix[stamp, 'n_samples'] = n_samples
        if 'support_size' in inv_columns: df_inv.ix[stamp, 'support_size'] = str(support_size)
        if 'sets_per_cell' in inv_columns: df_inv.ix[stamp, 'sets_per_cell'] = sets_per_cell
        if 'max_features' in inv_columns: df_inv.ix[stamp, 'max_features'] = max_features
        info_dir = os.path.dirname(inventory_txt)
        existing_models = fnmatch.filter(os.listdir(info_dir), '%s*' % target_col)
        if len(existing_models) > 0:
            df_inv = df_inv[df_inv.index.isin(existing_models)]#'''


        if 'avg_oob' in inv_columns and make_oob_map: df_inv.ix[stamp, 'avg_oob'] = avg_oob
        if 'avg_count' in inv_columns and make_oob_map: df_inv.ix[stamp, 'avg_count'] = avg_cnt
        if len(df_inv) > 1:
            df_inv.to_csv(inventory_txt, sep='\t')
        else:
            print 'WARNING: Model info not written to inventory_txt...\n' #'''   
        
    
    print 'Total training time: %.1f minutes' % ((time.time() - t0)/60)

if __name__ == '__main__':
     params = sys.argv[1]
     sys.exit(main(params))

#params = '/vol/v2/stem/jdb_test/param_files/train_stem_params.txt'




