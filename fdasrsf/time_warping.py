"""
Group-wise function alignment using SRSF framework and Dynamic Programming

moduleauthor:: Derek Tucker <jdtuck@sandia.gov>

"""
import numpy as np
import matplotlib.pyplot as plt
import fdasrsf.utility_functions as uf
import fdasrsf.fPCA as fpca
import fdasrsf.geometry as geo
from scipy.integrate import trapz, cumtrapz
from scipy.linalg import svd
from numpy.linalg import norm
from joblib import Parallel, delayed
from fdasrsf.fPLS import pls_svd
import fdasrsf.plot_style as plot
import fpls_warp as fpls
import collections


class fdawarp:
    """
    This class provides alignment methods for functional data using the SRVF framework

    Usage:  obj = fdawarp(f,t)
    
    :param f: (M,N): matrix defining N functions of M samples
    :param time: time vector of length M
    :param fn: aligned functions
    :param qn: aligned srvfs
    :param q0: initial srvfs
    :param fmean: function mean
    :param mqn: mean srvf
    :param gam: warping functions
    :param psi: srvf of warping functions
    :param stats: alignment statistics
    :param qun: cost function
    :param lambda: lambda
    :param method: optimization method
    :param gamI: invserse warping function
    :param rsamps: random samples
    :param fs: random aligned functions
    :param gams: random warping functions
    :param ft: random warped functions
    :param qs: random aligned srvfs
    :param type: alignment type
    :param mcmc: mcmc output if bayesian 
    
    Author :  J. D. Tucker (JDT) <jdtuck AT sandia.gov>
    Date   :  15-Mar-2018
    """

    def __init__(self, f, time):
        """
        Construct an instance of the fdawarp class
        :param f: numpy ndarray of shape (M,N) of N functions with M samples
        :param time: vector of size M describing the sample points
        """
        a = time.shape[0]

        if f.shape[0] != a:
            raise Exception('Columns of f and time must be equal')

        self.f = f
        self.time = time
        self.rsamps = False
    

    def srsf_align(self, method="mean", omethod="DP", smoothdata=False, parallel=False, lam=0.0):
        """
        This function aligns a collection of functions using the elastic
        square-root slope (srsf) framework.

        :param method: (string) warp calculate Karcher Mean or Median (options = "mean" or "median") (default="mean")
        :param omethod: optimization method (DP, DP2, RBFGS) (default = DP)
        :param smoothdata: Smooth the data using a box filter (default = F)
        :param parallel: run in parallel (default = F)
        :param lam: controls the elasticity (default = 0)
        :type lam: double
        :type smoothdata: bool

        Examples
        >>> import tables
        >>> fun=tables.open_file("../Data/simu_data.h5")
        >>> f = fun.root.f[:]
        >>> f = f.transpose()
        >>> time = fun.root.time[:]
        >>> obj = fs.fdawarp(f,time)
        >>> obj.srsf_align()

        """
        M = self.f.shape[0]
        N = self.f.shape[1]
        self.lam = lam

        if M > 500:
            parallel = True
        elif N > 100:
            parallel = True

        eps = np.finfo(np.double).eps
        f0 = self.f

        methods = ["mean", "median"]
        self.type = method

        # 0 mean, 1-median
        method = [i for i, x in enumerate(methods) if x == method]
        if len(method) == 0:
            method = 0
        else:
            method = method[0]

        # Compute SRSF function from data
        f, g, g2 = uf.gradient_spline(self.time, self.f, smoothdata)
        q = g / np.sqrt(abs(g) + eps)

        print("Initializing...")
        mnq = q.mean(axis=1)
        a = mnq.repeat(N)
        d1 = a.reshape(M, N)
        d = (q - d1) ** 2
        dqq = np.sqrt(d.sum(axis=0))
        min_ind = dqq.argmin()
        mq = q[:, min_ind]
        mf = f[:, min_ind]

        if parallel:
            out = Parallel(n_jobs=-1)(delayed(uf.optimum_reparam)(mq, self.time,
                                    q[:, n], omethod, lam, mf[0], f[0,n]) for n in range(N))
            gam = np.array(out)
            gam = gam.transpose()
        else:
            gam = np.zeros((M,N))
            for k in range(0,N):
                gam[:,k] = uf.optimum_reparam(mq,self.time,q[:,k],omethod,lam,mf[0],f[0,k])

        gamI = uf.SqrtMeanInverse(gam)
        mf = np.interp((self.time[-1] - self.time[0]) * gamI + self.time[0], self.time, mf)
        mq = uf.f_to_srsf(mf, self.time)

        # Compute Karcher Mean
        if method == 0:
            print("Compute Karcher Mean of %d function in SRSF space..." % N)
        if method == 1:
            print("Compute Karcher Median of %d function in SRSF space..." % N)

        MaxItr = 20
        ds = np.repeat(0.0, MaxItr + 2)
        ds[0] = np.inf
        qun = np.repeat(0.0, MaxItr + 1)
        tmp = np.zeros((M, MaxItr + 2))
        tmp[:, 0] = mq
        mq = tmp
        tmp = np.zeros((M, MaxItr+2))
        tmp[:,0] = mf
        mf = tmp
        tmp = np.zeros((M, N, MaxItr + 2))
        tmp[:, :, 0] = self.f
        f = tmp
        tmp = np.zeros((M, N, MaxItr + 2))
        tmp[:, :, 0] = q
        q = tmp

        for r in range(0, MaxItr):
            print("updating step: r=%d" % (r + 1))
            if r == (MaxItr - 1):
                print("maximal number of iterations is reached")

            # Matching Step
            if parallel:
                out = Parallel(n_jobs=-1)(delayed(uf.optimum_reparam)(mq[:, r],
                                        self.time, q[:, n, 0], omethod, lam, mf[0,r],
                                        f[0,k,0] ) for n in range(N))
                gam = np.array(out)
                gam = gam.transpose()
            else:
                for k in range(0,N):
                    gam[:,k] = uf.optimum_reparam(mq[:, r], self.time, q[:, k, 0],
                            omethod, lam, mf[0,r], f[0,k,0])

            gam_dev = np.zeros((M, N))
            vtil = np.zeros((M,N))
            dtil = np.zeros(N)
            for k in range(0, N):
                f[:, k, r + 1] = np.interp((self.time[-1] - self.time[0]) * gam[:, k]
                                        + self.time[0], self.time, f[:, k, 0])
                q[:, k, r + 1] = uf.f_to_srsf(f[:, k, r + 1], self.time)
                gam_dev[:, k] = np.gradient(gam[:, k], 1 / float(M - 1))
                v = q[:, k, r + 1] - mq[:,r]
                d = np.sqrt(trapz(v*v, self.time))
                vtil[:,k] = v/d
                dtil[k] = 1.0/d

            mqt = mq[:, r]
            a = mqt.repeat(N)
            d1 = a.reshape(M, N)
            d = (q[:, :, r + 1] - d1) ** 2
            if method == 0:
                d1 = sum(trapz(d, self.time, axis=0))
                d2 = sum(trapz((1 - np.sqrt(gam_dev)) ** 2, self.time, axis=0))
                ds_tmp = d1 + lam * d2
                ds[r + 1] = ds_tmp

                # Minimization Step
                # compute the mean of the matched function
                qtemp = q[:, :, r + 1]
                ftemp = f[:, :, r + 1]
                mq[:, r + 1] = qtemp.mean(axis=1)
                mf[:, r + 1] = ftemp.mean(axis=1)

                qun[r] = norm(mq[:, r + 1] - mq[:, r]) / norm(mq[:, r])

            if method == 1:
                d1 = np.sqrt(sum(trapz(d, self.time, axis=0)))
                d2 = sum(trapz((1 - np.sqrt(gam_dev)) ** 2, self.time, axis=0))
                ds_tmp = d1 + lam * d2
                ds[r + 1] = ds_tmp

                # Minimization Step
                # compute the mean of the matched function
                stp = .3
                vbar = vtil.sum(axis=1)*(1/dtil.sum())
                qtemp = q[:, :, r + 1] 
                ftemp = f[:, :, r + 1] 
                mq[:, r + 1] = mq[:,r] + stp*vbar
                tmp = np.zeros(M)
                tmp[1:] = cumtrapz(mq[:, r + 1] * np.abs(mq[:, r + 1]), self.time)
                mf[:, r + 1] = np.median(f0[1, :])+tmp

                qun[r] = norm(mq[:, r + 1] - mq[:, r]) / norm(mq[:, r])

            if qun[r] < 1e-2 or r >= MaxItr:
                break

        # Last Step with centering of gam
        r += 1
        if parallel:
            out = Parallel(n_jobs=-1)(delayed(uf.optimum_reparam)(mq[:, r], self.time,
                q[:, n, 0], omethod, lam, mf[0,r], f[0,n,0]) for n in range(N))
            gam = np.array(out)
            gam = gam.transpose()
        else:
            for k in range(0,N):
                gam[:,k] = uf.optimum_reparam(mq[:, r], self.time, q[:, k, 0], omethod,
                        lam, mf[0,r], f[0,k,0])

        gam_dev = np.zeros((M, N))
        for k in range(0, N):
            gam_dev[:, k] = np.gradient(gam[:, k], 1 / float(M - 1))

        gamI = uf.SqrtMeanInverse(gam)
        gamI_dev = np.gradient(gamI, 1 / float(M - 1))
        time0 = (self.time[-1] - self.time[0]) * gamI + self.time[0]
        mq[:, r + 1] = np.interp(time0, self.time, mq[:, r]) * np.sqrt(gamI_dev)

        for k in range(0, N):
            q[:, k, r + 1] = np.interp(time0, self.time, q[:, k, r]) * np.sqrt(gamI_dev)
            f[:, k, r + 1] = np.interp(time0, self.time, f[:, k, r])
            gam[:, k] = np.interp(time0, self.time, gam[:, k])

        # Aligned data & stats
        self.fn = f[:, :, r + 1]
        self.qn = q[:, :, r + 1]
        self.q0 = q[:, :, 0]
        mean_f0 = f0.mean(axis=1)
        std_f0 = f0.std(axis=1)
        mean_fn = self.fn.mean(axis=1)
        std_fn = self.fn.std(axis=1)
        self.gam = gam
        self.mqn = mq[:, r + 1]
        tmp = np.zeros(M)
        tmp[1:] = cumtrapz(self.mqn * np.abs(self.mqn), self.time)
        self.fmean = np.mean(f0[1, :]) + tmp

        fgam = np.zeros((M, N))
        for k in range(0, N):
            time0 = (self.time[-1] - self.time[0]) * gam[:, k] + self.time[0]
            fgam[:, k] = np.interp(time0, self.time, self.fmean)

        var_fgam = fgam.var(axis=1)
        self.orig_var = trapz(std_f0 ** 2, self.time)
        self.amp_var = trapz(std_fn ** 2, self.time)
        self.phase_var = trapz(var_fgam, self.time)

        return


    def plot(self):
        """
        plot plot functional alignment results
        
        Usage: obj.plot()
        """

        M = self.f.shape[0]
        plot.f_plot(self.time, self.f, title="f Original Data")

        fig, ax = plot.f_plot(np.arange(0, M) / float(M - 1), self.gam,
                                title="Warping Functions")
        ax.set_aspect('equal')

        plot.f_plot(self.time, self.fn, title="Warped Data")

        mean_f0 = self.f.mean(axis=1)
        std_f0 = self.f.std(axis=1)
        mean_fn = self.fn.mean(axis=1)
        std_fn = self.fn.std(axis=1)
        tmp = np.array([mean_f0, mean_f0 + std_f0, mean_f0 - std_f0])
        tmp = tmp.transpose()
        plot.f_plot(self.time, tmp, title="Original Data: Mean $\pm$ STD")

        tmp = np.array([mean_fn, mean_fn + std_fn, mean_fn - std_fn])
        tmp = tmp.transpose()
        plot.f_plot(self.time, tmp, title="Warped Data: Mean $\pm$ STD")

        plot.f_plot(self.time, self.fmean, title="$f_{mean}$")
        plt.show()

        return
    
    def gauss_model(self, n=1, sort_samples=False):
        """
        This function models the functional data using a Gaussian model
        extracted from the principal components of the srvfs

        :param n: number of random samples
        :param sort_samples: sort samples (default = T)
        :type n: integer
        :type sort_samples: bool
        """
        fn = self.fn
        time = self.time
        qn = self.qn
        gam = self.gam

        # Parameters
        eps = np.finfo(np.double).eps
        binsize = np.diff(time)
        binsize = binsize.mean()
        M = time.size

        # compute mean and covariance in q-domain
        mq_new = qn.mean(axis=1)
        mididx = np.round(time.shape[0] / 2)
        m_new = np.sign(fn[mididx, :]) * np.sqrt(np.abs(fn[mididx, :]))
        mqn = np.append(mq_new, m_new.mean())
        qn2 = np.vstack((qn, m_new))
        C = np.cov(qn2)

        q_s = np.random.multivariate_normal(mqn, C, n)
        q_s = q_s.transpose()

        # compute the correspondence to the original function domain
        fs = np.zeros((M, n))
        for k in range(0, n):
            fs[:, k] = uf.cumtrapzmid(time, q_s[0:M, k] * np.abs(q_s[0:M, k]),
                                    np.sign(q_s[M, k]) * (q_s[M, k] ** 2),
                                    mididx)

        fbar = fn.mean(axis=1)
        fsbar = fs.mean(axis=1)
        err = np.transpose(np.tile(fbar-fsbar, (n,1)))
        fs += err

        # random warping generation
        rgam = uf.randomGamma(gam, n)
        gams = np.zeros((M, n))
        for k in range(0, n):
            gams[:, k] = uf.invertGamma(rgam[:, k])

        # sort functions and warping
        if sort_samples:
            mx = fs.max(axis=0)
            seq1 = mx.argsort()

            # compute the psi-function
            fy = np.gradient(rgam, binsize)
            psi = fy / np.sqrt(abs(fy) + eps)
            ip = np.zeros(n)
            len = np.zeros(n)
            for i in range(0, n):
                tmp = np.ones(M)
                ip[i] = tmp.dot(psi[:, i] / M)
                len[i] = np.arccos(tmp.dot(psi[:, i] / M))

            seq2 = len.argsort()

            # combine x-variability and y-variability
            ft = np.zeros((M, n))
            for k in range(0, n):
                ft[:, k] = np.interp(gams[:, seq2[k]], np.arange(0, M) /
                                    np.double(M - 1), fs[:, seq1[k]])
                tmp = np.isnan(ft[:, k])
                while tmp.any():
                    rgam2 = uf.randomGamma(gam, 1)
                    ft[:, k] = np.interp(gams[:, seq2[k]], np.arange(0, M) /
                                        np.double(M - 1), uf.invertGamma(rgam2))
        else:
            # combine x-variability and y-variability
            ft = np.zeros((M, n))
            for k in range(0, n):
                ft[:, k] = np.interp(gams[:, k], np.arange(0, M) /
                                    np.double(M - 1), fs[:, k])
                tmp = np.isnan(ft[:, k])
                while tmp.any():
                    rgam2 = uf.randomGamma(gam, 1)
                    ft[:, k] = np.interp(gams[:, k], np.arange(0, M) /
                                        np.double(M - 1), uf.invertGamma(rgam2))

        
        self.rsamps = True
        self.fs = fs
        self.gams = rgam
        self.ft = ft
        self.qs = q_s[0:M,:]

        return


    def joint_gauss_model(self, n=1, no=3):
        """
        This function models the functional data using a joint Gaussian model
        extracted from the principal components of the srsfs

        :param n: number of random samples
        :param no: number of principal components (default = 3)
        :type n: integer
        :type no: integer
        """

        # Parameters
        fn = self.fn
        time = self.time
        qn = self.qn
        gam = self.gam

        M = time.size

        # Perform PCA
        jfpca = fpca.fdajpca(self)
        jfpca.calc_fpca(no=no)
        s = jfpca.latent
        U = jfpca.U
        C = jfpca.C
        mu_psi = jfpca.mu_psi

        # compute mean and covariance
        mq_new = qn.mean(axis=1)
        mididx = jfpca.id
        m_new = np.sign(fn[mididx, :]) * np.sqrt(np.abs(fn[mididx, :]))
        mqn = np.append(mq_new, m_new.mean())

        # generate random samples
        vals = np.random.multivariate_normal(np.zeros(s.shape), np.diag(s), n)
        
        tmp = np.matmul(U, np.transpose(vals))
        qhat = np.tile(mqn.T,(n,1)).T + tmp[0:M+1,:]
        tmp = np.matmul(U, np.transpose(vals)/C)
        vechat = tmp[(M+1):,:]
        psihat = np.zeros((M,n))
        gamhat = np.zeros((M,n))
        for ii in range(n):
            psihat[:,ii] = geo.exp_map(mu_psi,vechat[:,ii])
            gam_tmp = cumtrapz(psihat[:,ii]**2,np.linspace(0,1,M),initial=0.0)
            gamhat[:,ii] = (gam_tmp - gam_tmp.min())/(gam_tmp.max()-gam_tmp.min())
        
        ft = np.zeros((M,n))
        fhat = np.zeros((M,n))
        for ii in range(n):
            fhat[:,ii] = uf.cumtrapzmid(time, qhat[0:M,ii]*np.fabs(qhat[0:M,ii]), np.sign(qhat[M,ii])*(qhat[M,ii]*qhat[M,ii]), mididx)
            ft[:,ii] = uf.warp_f_gamma(np.linspace(0,1,M),fhat[:,ii],gamhat[:,ii])


        self.rsamps = True
        self.fs = fhat
        self.gams = gamhat
        self.ft = ft
        self.qs = qhat[0:M,:]

        return


def align_fPCA(f, time, num_comp=3, showplot=True, smoothdata=False):
    """
    aligns a collection of functions while extracting principal components.
    The functions are aligned to the principal components

    :param f: numpy ndarray of shape (M,N) of N functions with M samples
    :param time: vector of size M describing the sample points
    :param num_comp: number of fPCA components
    :param showplot: Shows plots of results using matplotlib (default = T)
    :param smooth_data: Smooth the data using a box filter (default = F)
    :param sparam: Number of times to run box filter (default = 25)
    :type sparam: double
    :type smooth_data: bool
    :type f: np.ndarray
    :type time: np.ndarray

    :rtype: tuple of numpy array
    :return fn: aligned functions - numpy ndarray of shape (M,N) of N
                functions with M samples
    :return qn: aligned srvfs - similar structure to fn
    :return q0: original srvf - similar structure to fn
    :return mqn: srvf mean or median - vector of length M
    :return gam: warping functions - similar structure to fn
    :return q_pca: srsf principal directions
    :return f_pca: functional principal directions
    :return latent: latent values
    :return coef: coefficients
    :return U: eigenvectors
    :return orig_var: Original Variance of Functions
    :return amp_var: Amplitude Variance
    :return phase_var: Phase Variance

    """
    lam = 0.0
    MaxItr = 50
    coef = np.arange(-2., 3.)
    Nstd = coef.shape[0]
    M = f.shape[0]
    N = f.shape[1]
    if M > 500:
        parallel = True
    elif N > 100:
        parallel = True
    else:
        parallel = False

    eps = np.finfo(np.double).eps
    f0 = f

    if showplot:
        plot.f_plot(time, f, title="Original Data")

    # Compute SRSF function from data
    f, g, g2 = uf.gradient_spline(time, f, smoothdata)
    q = g / np.sqrt(abs(g) + eps)

    print ("Initializing...")
    mnq = q.mean(axis=1)
    a = mnq.repeat(N)
    d1 = a.reshape(M, N)
    d = (q - d1) ** 2
    dqq = np.sqrt(d.sum(axis=0))
    min_ind = dqq.argmin()

    print("Aligning %d functions in SRVF space to %d fPCA components..."
          % (N, num_comp))
    itr = 0
    mq = np.zeros((M, MaxItr + 1))
    mq[:, itr] = q[:, min_ind]
    fi = np.zeros((M, N, MaxItr + 1))
    fi[:, :, 0] = f
    qi = np.zeros((M, N, MaxItr + 1))
    qi[:, :, 0] = q
    gam = np.zeros((M, N, MaxItr + 1))
    cost = np.zeros(MaxItr + 1)

    while itr < MaxItr:
        print("updating step: r=%d" % (itr + 1))
        if itr == MaxItr:
            print("maximal number of iterations is reached")

        # PCA Step
        a = mq[:, itr].repeat(N)
        d1 = a.reshape(M, N)
        qhat_cent = qi[:, :, itr] - d1
        K = np.cov(qi[:, :, itr])
        U, s, V = svd(K)

        alpha_i = np.zeros((num_comp, N))
        for ii in range(0, num_comp):
            for jj in range(0, N):
                alpha_i[ii, jj] = trapz(qhat_cent[:, jj] * U[:, ii], time)

        U1 = U[:, 0:num_comp]
        tmp = U1.dot(alpha_i)
        qhat = d1 + tmp

        # Matching Step
        if parallel:
            out = Parallel(n_jobs=-1)(
                delayed(uf.optimum_reparam)(qhat[:, n], time, qi[:, n, itr],
                                            "DP", lam) for n in range(N))
            gam_t = np.array(out)
            gam[:, :, itr] = gam_t.transpose()
        else:
            gam[:, :, itr] = uf.optimum_reparam(qhat, time, qi[:, :, itr], "DP",  lam)

        for k in range(0, N):
            time0 = (time[-1] - time[0]) * gam[:, k, itr] + time[0]
            fi[:, k, itr + 1] = np.interp(time0, time, fi[:, k, itr])
            qi[:, k, itr + 1] = uf.f_to_srsf(fi[:, k, itr + 1], time)

        qtemp = qi[:, :, itr + 1]
        mq[:, itr + 1] = qtemp.mean(axis=1)

        cost_temp = np.zeros(N)

        for ii in range(0, N):
            cost_temp[ii] = norm(qtemp[:, ii] - qhat[:, ii]) ** 2

        cost[itr + 1] = cost_temp.mean()

        if abs(cost[itr + 1] - cost[itr]) < 1e-06:
            break

        itr += 1

    if itr >= MaxItr:
        itrf = MaxItr
    else:
        itrf = itr+1
    cost = cost[1:(itrf+1)]

    # Aligned data & stats
    fn = fi[:, :, itrf]
    qn = qi[:, :, itrf]
    q0 = qi[:, :, 0]
    mean_f0 = f0.mean(axis=1)
    std_f0 = f0.std(axis=1)
    mqn = mq[:, itrf]
    gamf = gam[:, :, 0]
    for k in range(1, itr):
        gam_k = gam[:, :, k]
        for l in range(0, N):
            time0 = (time[-1] - time[0]) * gam_k[:, l] + time[0]
            gamf[:, l] = np.interp(time0, time, gamf[:, l])

    # Center Mean
    gamI = uf.SqrtMeanInverse(gamf)
    gamI_dev = np.gradient(gamI, 1 / float(M - 1))
    time0 = (time[-1] - time[0]) * gamI + time[0]
    mqn = np.interp(time0, time, mqn) * np.sqrt(gamI_dev)
    for k in range(0, N):
        qn[:, k] = np.interp(time0, time, qn[:, k]) * np.sqrt(gamI_dev)
        fn[:, k] = np.interp(time0, time, fn[:, k])
        gamf[:, k] = np.interp(time0, time, gamf[:, k])

    mean_fn = fn.mean(axis=1)
    std_fn = fn.std(axis=1)

    # Get Final PCA
    mididx = np.round(time.shape[0] / 2)
    m_new = np.sign(fn[mididx, :]) * np.sqrt(np.abs(fn[mididx, :]))
    mqn2 = np.append(mqn, m_new.mean())
    qn2 = np.vstack((qn, m_new))
    K = np.cov(qn2)

    U, s, V = svd(K)
    stdS = np.sqrt(s)

    # compute the PCA in the q domain
    q_pca = np.ndarray(shape=(M + 1, Nstd, num_comp), dtype=float)
    for k in range(0, num_comp):
        for l in range(0, Nstd):
            q_pca[:, l, k] = mqn2 + coef[l] * stdS[k] * U[:, k]

    # compute the correspondence in the f domain
    f_pca = np.ndarray(shape=(M, Nstd, num_comp), dtype=float)
    for k in range(0, num_comp):
        for l in range(0, Nstd):
            q_pca_tmp = q_pca[0:M, l, k] * np.abs(q_pca[0:M, l, k])
            q_pca_tmp2 = np.sign(q_pca[M, l, k]) * (q_pca[M, l, k] ** 2)
            f_pca[:, l, k] = uf.cumtrapzmid(time, q_pca_tmp, q_pca_tmp2, np.floor(time.shape[0]/2))

    N2 = qn.shape[1]
    c = np.zeros((N2, num_comp))
    for k in range(0, num_comp):
        for l in range(0, N2):
            c[l, k] = sum((np.append(qn[:, l], m_new[l]) - mqn2) * U[:, k])

    if showplot:
        CBcdict = {
            'Bl': (0, 0, 0),
            'Or': (.9, .6, 0),
            'SB': (.35, .7, .9),
            'bG': (0, .6, .5),
            'Ye': (.95, .9, .25),
            'Bu': (0, .45, .7),
            'Ve': (.8, .4, 0),
            'rP': (.8, .6, .7),
        }
        cl = sorted(CBcdict.keys())

        # Align Plots
        fig, ax = plot.f_plot(np.arange(0, M) / float(M - 1), gamf,
                              title="Warping Functions")
        ax.set_aspect('equal')

        plot.f_plot(time, fn, title="Warped Data")

        tmp = np.array([mean_f0, mean_f0 + std_f0, mean_f0 - std_f0])
        tmp = tmp.transpose()
        plot.f_plot(time, tmp, title="Original Data: Mean $\pm$ STD")

        tmp = np.array([mean_fn, mean_fn + std_fn, mean_fn - std_fn])
        tmp = tmp.transpose()
        plot.f_plot(time, tmp, title="Warped Data: Mean $\pm$ STD")

        # PCA Plots
        fig, ax = plt.subplots(2, num_comp)
        for k in range(0, num_comp):
            axt = ax[0, k]
            for l in range(0, Nstd):
                axt.plot(time, q_pca[0:M, l, k], color=CBcdict[cl[l]])
                axt.hold(True)

            axt.set_title('q domain: PD %d' % (k + 1))
            plot.rstyle(axt)
            axt = ax[1, k]
            for l in range(0, Nstd):
                axt.plot(time, f_pca[:, l, k], color=CBcdict[cl[l]])
                axt.hold(True)

            axt.set_title('f domain: PD %d' % (k + 1))
            plot.rstyle(axt)
        fig.set_tight_layout(True)

        cumm_coef = 100 * np.cumsum(s) / sum(s)
        idx = np.arange(0, M + 1) + 1
        plot.f_plot(idx, cumm_coef, "Coefficient Cumulative Percentage")
        plt.xlabel("Percentage")
        plt.ylabel("Index")
        plt.show()

    mean_f0 = f0.mean(axis=1)
    std_f0 = f0.std(axis=1)
    mean_fn = fn.mean(axis=1)
    std_fn = fn.std(axis=1)
    tmp = np.zeros(M)
    tmp[1:] = cumtrapz(mqn * np.abs(mqn), time)
    fmean = np.mean(f0[1, :]) + tmp

    fgam = np.zeros((M, N))
    for k in range(0, N):
        time0 = (time[-1] - time[0]) * gamf[:, k] + time[0]
        fgam[:, k] = np.interp(time0, time, fmean)

    var_fgam = fgam.var(axis=1)
    orig_var = trapz(std_f0 ** 2, time)
    amp_var = trapz(std_fn ** 2, time)
    phase_var = trapz(var_fgam, time)

    K = np.cov(fn)

    U, s, V = svd(K)

    align_fPCAresults = collections.namedtuple('align_fPCA', ['fn', 'qn',
                                               'q0', 'mqn', 'gam', 'q_pca',
                                               'f_pca', 'latent', 'coef',
                                               'U', 'orig_var', 'amp_var',
                                               'phase_var', 'cost'])

    out = align_fPCAresults(fn, qn, q0, mqn, gamf, q_pca, f_pca, s, c,
                            U, orig_var, amp_var, phase_var, cost)
    return out


def align_fPLS(f, g, time, comps=3, showplot=True, smoothdata=False,
               delta=0.01, max_itr=100):
    """
    This function aligns a collection of functions while performing
    principal least squares

    :param f: numpy ndarray of shape (M,N) of N functions with M samples
    :param g: numpy ndarray of shape (M,N) of N functions with M samples
    :param time: vector of size M describing the sample points
    :param comps: number of fPLS components
    :param showplot: Shows plots of results using matplotlib (default = T)
    :param smooth_data: Smooth the data using a box filter (default = F)
    :param delta: gradient step size
    :param max_itr: maximum number of iterations
    :type smooth_data: bool
    :type f: np.ndarray
    :type g: np.ndarray
    :type time: np.ndarray

    :rtype: tuple of numpy array
    :return fn: aligned functions - numpy ndarray of shape (M,N) of N
    functions with M samples
    :return gn: aligned functions - numpy ndarray of shape (M,N) of N
    functions with M samples
    :return qfn: aligned srvfs - similar structure to fn
    :return qgn: aligned srvfs - similar structure to fn
    :return qf0: original srvf - similar structure to fn
    :return qg0: original srvf - similar structure to fn
    :return gam: warping functions - similar structure to fn
    :return wqf: srsf principal weight functions
    :return wqg: srsf principal weight functions
    :return wf: srsf principal weight functions
    :return wg: srsf principal weight functions
    :return cost: cost function value

    """
    print ("Initializing...")
    binsize = np.diff(time)
    binsize = binsize.mean()
    eps = np.finfo(np.double).eps
    M = f.shape[0]
    N = f.shape[1]
    f0 = f
    g0 = g

    if showplot:
        plot.f_plot(time, f, title="f Original Data")
        plot.f_plot(time, g, title="g Original Data")

    # Compute q-function of f and g
    f, g1, g2 = uf.gradient_spline(time, f, smoothdata)
    qf = g1 / np.sqrt(abs(g1) + eps)
    g, g1, g2 = uf.gradient_spline(time, g, smoothdata)
    qg = g1 / np.sqrt(abs(g1) + eps)

    print("Calculating fPLS weight functions for %d Warped Functions..." % N)
    itr = 0
    fi = np.zeros((M, N, max_itr + 1))
    fi[:, :, itr] = f
    gi = np.zeros((M, N, max_itr + 1))
    gi[:, :, itr] = g
    qfi = np.zeros((M, N, max_itr + 1))
    qfi[:, :, itr] = qf
    qgi = np.zeros((M, N, max_itr + 1))
    qgi[:, :, itr] = qg
    wqf1, wqg1, alpha, values, costmp = pls_svd(time, qfi[:, :, itr],
                                                qgi[:, :, itr], 2, 0)
    wqf = np.zeros((M, max_itr + 1))
    wqf[:, itr] = wqf1[:, 0]
    wqg = np.zeros((M, max_itr + 1))
    wqg[:, itr] = wqg1[:, 0]
    gam = np.zeros((M, N, max_itr + 1))
    tmp = np.tile(np.linspace(0, 1, M), (N, 1))
    gam[:, :, itr] = tmp.transpose()
    wqf_diff = np.zeros(max_itr + 1)
    cost = np.zeros(max_itr + 1)
    cost_diff = 1

    while itr <= max_itr:

        # warping
        gamtmp = np.ascontiguousarray(gam[:, :, 0])
        qftmp = np.ascontiguousarray(qfi[:, :, 0])
        qgtmp = np.ascontiguousarray(qgi[:, :, 0])
        wqftmp = np.ascontiguousarray(wqf[:, itr])
        wqgtmp = np.ascontiguousarray(wqg[:, itr])
        gam[:, :, itr + 1] = fpls.fpls_warp(time, gamtmp, qftmp, qgtmp,
                                            wqftmp, wqgtmp, display=0,
                                            delta=delta, tol=1e-6,
                                            max_iter=4000)

        for k in range(0, N):
            gam_k = gam[:, k, itr + 1]
            time0 = (time[-1] - time[0]) * gam_k + time[0]
            fi[:, k, itr + 1] = np.interp(time0, time, fi[:, k, 0])
            gi[:, k, itr + 1] = np.interp(time0, time, gi[:, k, 0])
            qfi[:, k, itr + 1] = uf.warp_q_gamma(time, qfi[:, k, 0], gam_k)
            qgi[:, k, itr + 1] = uf.warp_q_gamma(time, qgi[:, k, 0], gam_k)

        # PLS
        wqfi, wqgi, alpha, values, costmp = pls_svd(time, qfi[:, :, itr + 1],
                                                    qgi[:, :, itr + 1], 2, 0)
        wqf[:, itr + 1] = wqfi[:, 0]
        wqg[:, itr + 1] = wqgi[:, 0]

        wqf_diff[itr] = np.sqrt(sum(wqf[:, itr + 1] - wqf[:, itr]) ** 2)

        rfi = np.zeros(N)
        rgi = np.zeros(N)

        for l in range(0, N):
            rfi[l] = uf.innerprod_q(time, qfi[:, l, itr + 1], wqf[:, itr + 1])
            rgi[l] = uf.innerprod_q(time, qgi[:, l, itr + 1], wqg[:, itr + 1])

        cost[itr] = np.cov(rfi, rgi)[1, 0]

        if itr > 1:
            cost_diff = cost[itr] - cost[itr - 1]

        print("Iteration: %d - Diff Value: %f - %f" % (itr + 1, wqf_diff[itr],
                                                       cost[itr]))
        if wqf_diff[itr] < 1e-1 or abs(cost_diff) < 1e-3:
            break

        itr += 1

    cost = cost[0:(itr + 1)]

    # Aligned data & stats
    fn = fi[:, :, itr + 1]
    gn = gi[:, :, itr + 1]
    qfn = qfi[:, :, itr + 1]
    qf0 = qfi[:, :, 0]
    qgn = qgi[:, :, itr + 1]
    qg0 = qgi[:, :, 0]
    wqfn, wqgn, alpha, values, costmp = pls_svd(time, qfn, qgn, comps, 0)

    wf = np.zeros((M, comps))
    wg = np.zeros((M, comps))
    for ii in range(0, comps):
        wf[:, ii] = cumtrapz(wqfn[:, ii] * np.abs(wqfn[:, ii]), time, initial=0)
        wg[:, ii] = cumtrapz(wqgn[:, ii] * np.abs(wqgn[:, ii]), time, initial=0)

    gam_f = gam[:, :, itr + 1]

    if showplot:
        # Align Plots
        fig, ax = plot.f_plot(np.arange(0, M) / float(M - 1), gam_f,
                              title="Warping Functions")
        ax.set_aspect('equal')

        plot.f_plot(time, fn, title="fn Warped Data")
        plot.f_plot(time, gn, title="gn Warped Data")
        plot.f_plot(time, wf, title="wf")
        plot.f_plot(time, wg, title="wg")

        plt.show()

    align_fPLSresults = collections.namedtuple('align_fPLS', ['wf', 'wg', 'fn',
                                               'gn', 'qfn', 'qgn', 'qf0',
                                               'qg0', 'wqf', 'wqg', 'gam',
                                               'values', 'cost'])

    out = align_fPLSresults(wf, wg, fn, gn, qfn, qgn, qf0, qg0, wqfn,
                            wqgn, gam_f, values, cost)
    return out
