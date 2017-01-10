import pkg_resources
import pickle
from pystan import StanModel
import numpy as np
from sklearn.metrics import log_loss

class LRHMC:
    """
    Methods for performing Bayesian logistic regression using Hamiltonian Monte Carlo

    Used to try and debug ZV control variates for sgld

    References:
    1. Hamiltonian Monte Carlo - https://arxiv.org/pdf/1206.1901.pdf
    2. ZV control variates for Hamiltonian Monte Carlo - 
        https://projecteuclid.org/download/pdfview_1/euclid.ba/1393251772
    """

    def __init__(self,X_train,X_test,y_train,y_test):
        """
        Initialise LRHMC object

        Parameters:
        X_train - matrix of explanatory variables for training (assumes numpy array of floats)
        X_test - matrix of explanatory variables for testing (assumes numpy array of ints)
        y_train - vector of response variables for training (assumes numpy array of ints)
        y_test - vector of response variables for testing (assumes numpy array of ints)
        """
        self.X = X_train
        self.y = y_train
        self.X_test = X_test
        self.y_test = y_test
        # Load STAN model object, if it doesn't exist create a new one
        try:
            self.stan_pkl = pkg_resources.resource_filename('logistic_control_variate', 'hmc/lr.pkl')
            with open( self.stan_pkl ) as stanbin:
                self.stan = pickle.load(stanbin)
        except ( IOError, EOFError ):
            stan_code = pkg_resources.resource_filename('logistic_control_variate', 'hmc/lr.stan')
            self.stan = StanModel( stan_code )

        # Set dimension constants
        self.N = self.X.shape[0]
        self.d = self.X.shape[1]
        self.test_size = self.X_test.shape[0]

        # Get data in the right format for STAN
        self.data = { 'N' : self.N,
                'D' : self.d,
                'y' : self.y,
                'X' : self.X }
        # Initialise data for fitting
        self.fitted = None
        self.sample = None
        self.logpost_sample = None
        self.n_iters = None


    def fit(self,n_iters=1000):
        """
        Fit HMC model to LogisticRegression object using STAN

        Parameters:
        lr - LogisticRegression object

        Modifies:
        self.fitted - updates to STAN fitted object
        self.sample - updates to the sampled MCMC chain
        self.logpost_sample - updates to the gradient at each point in the chain
        """
        self.n_iters = n_iters
        self.fitted = self.stan.sampling( data = self.data, iter = 2*self.n_iters, chains = 1 )
        # Dump model file once fit to avoid recompiling
        with open( self.stan_pkl, 'w' ) as stanbin:
            pickle.dump(self.stan, stanbin)
        self.sample = self.fitted.extract()['beta']
        self.logpost_sample = np.zeros( self.sample.shape )
        for i in range(self.n_iters):
            self.logpost_sample[i,:] = self.fitted.grad_log_prob( self.sample[i,:] )
        temp_file = pkg_resources.resource_filename(
                'logistic_control_variate', 'data/hmc_temp/fitted.pkl')
        with open(temp_file, 'w') as outfile:
            pickle.dump(self, outfile)

    
    def postprocess(self):
        """
        Postprocess MCMC chain with ZV control variates

        Requires:
        Fitted model - self.fitted, self.sample, self.logpost_sample is not None

        Modifies:
        self.sample - updates with postprocessed chain
        """
        sample_mean = np.mean( self.sample, axis = 0 )
        grad_mean = np.mean( self.logpost_sample, axis = 0 )
        var_grad = np.cov( self.logpost_sample, rowvar = 0 )

        # Initialise variables
        out_sample = np.zeros( self.sample.shape )
        a = np.zeros(self.d)
        current_cov = np.zeros(self.d)

        # Calculate new sample once control variates have been calculated
        for j in range(self.d):
            current_cov = np.zeros(self.d)
            for i in range(self.n_iters):
                current_cov += 1 / float(self.n_iters-1) * ( 
                        self.sample[i,j] - sample_mean[j] )*( self.logpost_sample[i,:] - grad_mean )
            a = - np.matmul( np.linalg.inv( var_grad ), current_cov )
            for i in range(self.n_iters):
                out_sample[i,j] = self.sample[i,j] + np.dot( a, self.logpost_sample[i,:] )

        # Calculate new log loss at a subsample of points
        oldll = self.logloss(self.sample)
        newll = self.logloss(out_sample)
        print "Old log loss: {0}\tNew log loss: {1}".format( oldll, newll )


    def logloss(self,sample):
        """
        Calculate the log loss on the test set for specified parameter values beta
        
        Parameters:
        beta - a vector of logistic regression parameters (float array)
        """
        logloss = 0
        for m in range(self.n_iters):
            y_pred = np.zeros(self.test_size, dtype = int)
            beta = np.squeeze( np.copy( sample[m,:] ) )
            for i in range(self.test_size):
                x = np.squeeze( np.copy( self.X_test[i,:] ) )
                y_pred[i] = int( np.dot( beta, x ) >= 0.0 )
            logloss += log_loss( self.y_test, y_pred ) / float( self.n_iters )
        return logloss
