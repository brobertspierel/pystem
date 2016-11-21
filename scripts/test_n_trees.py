import os
import sys
import shutil
import re
import pandas as pd
import numpy as np
from matplotlib import pyplot as plt
from datetime import datetime

import randomforest as forest


def test(out_dir, x_train, y_train, max_trees, step):
    ''' Make a plot for sample_txt of number of trees vs OOB error rates '''

    print 'Testing OOB error rate per number of trees...'
    oob_errors = []
    n_trees = range(1, max_trees + 1, step)
    n_tests = max_trees / step

    for i, n in enumerate(n_trees):
        print 'Testing %s of %s models' % (i + 1, n_tests)
        rf_model = forest.train_rf_regressor(x_train, y_train, ntrees=n)
        oob_errors.append(1 - rf_model.oob_score_)

    plt.plot(n_trees, oob_errors, '-')
    plt.axis([0, max_trees, 0, 1])
    plt.xlabel('Number of Trees')
    plt.ylabel('Out of Bag Error Rate')
    plt.title('Number of Decision Trees vs. OOB Error Rate')
    out_png = os.path.join(out_dir, 'ntrees_vs_oob_error.png')
    plt.savefig(out_png)
    plt.clf()

    print 'Plot PNG written to : ', out_png, '\n'


def main(params):

    # Read params and make variables from text
    inputs = forest.read_params(params)
    print inputs
    for i in inputs:
        print i, inputs[i]
        exec ("{0} = str({1})").format(i, inputs[i])

    # Check that variables were specified in params
    try:
        str_check = sample_txt, target_col, var_txt
        max_trees = int(max_trees)
        step = int(step)
    except NameError as e:
        print ''
        missing_var = str(e).split("'")[1]
        msg = "Variable '%s' not specified in param file:\n%s" % (missing_var, params)
        raise NameError(msg)

    # Raise an error if var_txt doesn't exist. Otherwise, just read it in
    if not os.path.exists(var_txt):
        print ''
        msg = 'var_text path specified does not exist:\n%s\n\n' % var_txt
        raise IOError(msg)
    df_var = pd.read_csv(var_txt, sep='\t', index_col='var_name')

    df_train = pd.read_csv(sample_txt, sep='\t', index_col='obs_id')
    predict_cols = sorted(np.unique([c for c in df_train.columns for v in df_var.index if v in c]))
    x_train = df_train.reindex(columns=predict_cols)
    y_train = df_train[target_col]

    out_dir = os.path.dirname(sample_txt)
    test(out_dir, x_train, y_train, max_trees, step)


if __name__ == '__main__':
    params = sys.argv[1]
    sys.exit(main(params))



