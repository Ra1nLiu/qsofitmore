#!/usr/bin/env python
# from mmap import MAP_ANONYMOUS
# from os import name
# from re import T
import sys
import glob
import matplotlib
import numpy as np
import matplotlib.pyplot as plt
import sfdmap
from scipy import interpolate
from scipy import integrate
from kapteyn import kmpfit
from PyAstronomy import pyasl
from astropy.io import fits
from astropy.cosmology import FlatLambdaCDM
from astropy.modeling.blackbody import blackbody_lambda
from astropy.table import Table
from astropy.coordinates import SkyCoord
from astropy import units as u
from PyQSOFit import QSOFit
from .extinction import *
import pkg_resources
# import pandas as pd

datapath = pkg_resources.resource_filename('PyQSOFit', '/')

__all__ = ['QSOFitNew']

class QSOFitNew(QSOFit):

    def __init__(self, lam, flux, err, z, ra=- 999., dec=-999., name=None, plateid=None, mjd=None, fiberid=None, 
                 path=None, and_mask=None, or_mask=None, is_sdss=True):
        """
        Get the input data perpared for the QSO spectral fitting
        
        Parameters:
        -----------
        lam: 1-D array with Npix
             Observed wavelength in unit of Angstrom
             
        flux: 1-D array with Npix
             Observed flux density in unit of 10^{-17} erg/s/cm^2/Angstrom
        
        err: 1-D array with Npix
             1 sigma err with the same unit of flux
             
        z: float number
            redshift
        
        ra, dec: float number, optional 
            the location of the source, right ascension and declination. The default number is 0
        name: str
            name of the object
        
        plateid, mjd, fiberid: integer number, optional
            If the source is SDSS object, they have the plate ID, MJD and Fiber ID in their file herader.
            
        path: str
            the path of the input data
            
        and_mask, or_mask: 1-D array with Npix, optional
            the bad pixels defined from SDSS data, which can be got from SDSS datacube.
        """
        
        self.lam = np.asarray(lam, dtype=np.float64)
        self.flux = np.asarray(flux, dtype=np.float64)
        self.err = np.asarray(err, dtype=np.float64)
        self.z = z
        self.and_mask = and_mask
        self.or_mask = or_mask
        self.ra = ra
        self.dec = dec
        self.name = name
        self.plateid = plateid
        self.mjd = mjd
        self.fiberid = fiberid
        self.path = path    
        self.is_sdss = is_sdss

    @classmethod
    def fromiraf(cls, fname, redshift=None, path=None, plateid=None, mjd=None, fiberid=None, 
                 ra=None, dec=None):
        """
        Initialize QSOFit object from a custom fits file
        generated by IRAF.
        Parameters:
        ----------
            fname : str
                name of the fits file.
            redshift : float
                redshift of the spectrum. Should be provided if not recorded in the fits header.
            path : str
                working directory.
        Returns:
        ----------
            cls : class
                A QSOFit object.
        Other parameters:
        ----------
            plateid, mjd, and fiberid: int
                Default None for non-SDSS spectra.
        Example:
        ----------
        q = QSOFit.fromiraf("custom_iraf_spectrum.fits", redshift=0.01, path=path)
        """
        hdu = fits.open(fname)
        header = hdu[0].header
        objname = header['object']
        # if plateid is None:
        #     plateid = 0
        # if mjd is None:
        #     mjd = 0
        # if fiberid is None:
        #     fiberid = 0
        if redshift is None:
            try:
                redshift = float(header['redshift'])
            except:
                print("Redshift not provided, setting redshift to zero.")
                redshift = 0
        if ra is None or dec is None:
            try:
                ra = float(header['ra'])
                dec = float(header['dec'])
            except:
                coord = SkyCoord(header['RA']+header['DEC'], 
                                 frame='icrs',
                                 unit=(u.hourangle, u.deg))
                ra = coord.ra.value
                dec = coord.dec.value
        if path is None:
            path = './'
        CRVAL1 = float(header['CRVAL1'])
        CD1_1 = float(header['CD1_1'])
        CRPIX1 = float(header['CRPIX1'])
        data = hdu[0].data
        dim = len(data.shape)
        if dim==1:
            l = len(data)
            wave = np.linspace(CRVAL1, 
                               CRVAL1 + (l - CRPIX1) * CD1_1, 
                               l)
            flux = data
            err = None
        elif dim==3:
            l = data.shape[2]
            print(repr(l))
            wave = np.linspace(CRVAL1, 
                               CRVAL1 + (l - CRPIX1) * CD1_1, 
                               l)
            flux = data[0,0,:]
            err = data[3,0,:]
        else:
            raise NotImplementedError("The IRAF spectrum has yet to be provided, not implemented.")
        hdu.close() 
        flux *= 1e17
        err *= 1e17
        return cls(lam=wave, flux=flux, err=err, z=redshift, ra=ra, dec=dec, name=objname, plateid=plateid, 
                   mjd=mjd, fiberid=fiberid, path=path, is_sdss=False)

    def setmapname(self, mapname):
        """
        Parameters:
            mapname : str
                name of the dust map. Currently only support
                'sfd' or 'planck'.
        """
        mapname = str(mapname).lower()
        self.mapname = mapname

    def _DeRedden(self, lam, flux, err, ra, dec, dustmap_path):
        """Correct the Galactic extinction"""
        try:
            print("The dust map is {}".format(self.mapname))
        except AttributeError:
            print('`mapname` for extinction not set.\nSetting `mapname` to `sfd`.')
            mapname = 'sfd'
            self.mapname = mapname
        if self.mapname == 'sfd':
            m = sfdmap.SFDMap(dustmap_path)
            zero_flux = np.where(flux == 0, True, False)
            flux[zero_flux] = 1e-10
            flux_unred = pyasl.unred(lam, flux, m.ebv(ra, dec))
            err_unred = err*flux_unred/flux
            flux_unred[zero_flux] = 0
            del self.flux, self.err
            self.flux = flux_unred
            self.err = err_unred
        elif self.mapname == 'planck':
            self.ebv = getebv(self.ra, self.dec, mapname=self.mapname)
            Alam = wang2019(self.lam, self.ebv)
            zero_flux = np.where(flux == 0, True, False)
            flux[zero_flux] = 1e-10
            flux_unred = deredden(Alam, self.flux) 
            err_unred = err*flux_unred/flux
            flux_unred[zero_flux] = 0
            del self.flux, self.err
            self.flux = flux_unred
            self.err = err_unred           
        return self.flux


    def _HostDecompose(self, wave, flux, err, z, Mi, npca_gal, npca_qso, path):
        path = datapath
        return super()._HostDecompose(wave, flux, err, z, Mi, npca_gal, npca_qso, path)

    
    def _DoContiFit(self, wave, flux, err, ra, dec, plateid, mjd, fiberid):
        if self.plateid is None:
            plateid = 0
        if self.plateid is None:
            mjd = 0
        if self.plateid is None:
            fiberid = 0
        tmp_selfpath = self.path
        self.path = datapath
        try:
            return super()._DoContiFit(wave, flux, err, ra, dec, plateid, mjd, fiberid)
        finally:
            self.path = tmp_selfpath

    def Fit(self, name=None, nsmooth=1, and_or_mask=True, reject_badpix=True, deredden=True, wave_range=None,
            wave_mask=None, decomposition_host=True, BC03=False, Mi=None, npca_gal=5, npca_qso=20, Fe_uv_op=True,
            Fe_flux_range=None, poly=False, BC=False, rej_abs=False, initial_guess=None, MC=True, n_trails=1,
            linefit=True, tie_lambda=True, tie_width=True, tie_flux_1=True, tie_flux_2=True, save_result=True,
            plot_fig=True, save_fig=True, plot_line_name=True, plot_legend=True, dustmap_path=None, save_fig_path=None,
            save_fits_path=None, save_fits_name=None):
        if name is None and save_fits_name is not None:
            name = save_fits_name
            print("Name is now {}.".format(name))
        elif name is None and save_fits_name is None:
            name = self.name
            print("Name is now {}.".format(name))
        else:
            pass
        if self.is_sdss == False and name is None:
            print("Bad figure name!")
        return super().Fit(name=name, nsmooth=nsmooth, and_or_mask=and_or_mask, reject_badpix=reject_badpix, 
                           deredden=deredden, wave_range=wave_range, wave_mask=wave_mask, 
                           decomposition_host=decomposition_host, BC03=BC03, Mi=Mi, npca_gal=npca_gal, 
                           npca_qso=npca_qso, Fe_uv_op=Fe_uv_op, Fe_flux_range=Fe_flux_range, poly=poly, 
                           BC=BC, rej_abs=rej_abs, initial_guess=initial_guess, MC=MC, n_trails=n_trails, 
                           linefit=linefit, tie_lambda=tie_lambda, tie_width=tie_width, tie_flux_1=tie_flux_1, 
                           tie_flux_2=tie_flux_2, save_result=save_result, plot_fig=plot_fig, 
                           save_fig=save_fig, plot_line_name=plot_line_name, plot_legend=plot_legend, 
                           dustmap_path=dustmap_path, save_fig_path=save_fig_path, save_fits_path=save_fits_path, 
                           save_fits_name=save_fits_name)


    def _PlotFig(self, ra, dec, z, wave, flux, err, decomposition_host, linefit, tmp_all, gauss_result, f_conti_model,
                 conti_fit, all_comp_range, uniq_linecomp_sort, line_flux, save_fig_path):
        """Plot the results"""
        
        self.PL_poly = conti_fit.params[6]*(wave/3000.0)**conti_fit.params[7]+self.F_poly_conti(wave,
                                                                                                conti_fit.params[11:])
        
        matplotlib.rc('xtick', labelsize=20)
        matplotlib.rc('ytick', labelsize=20)
        
        if linefit == True:
            fig, axn = plt.subplots(nrows=2, ncols=np.max([self.ncomp, 1]), figsize=(15, 8),
                                    squeeze=False)  # prepare for the emission line subplots in the second row
            ax = plt.subplot(2, 1, 1)  # plot the first subplot occupying the whole first row
            if self.MC == True:
                mc_flag = 2
            else:
                mc_flag = 1
            
            lines_total = np.zeros_like(wave)
            line_order = {'r': 3, 'g': 7}  # to make the narrow line plot above the broad line
            
            temp_gauss_result = gauss_result
            for p in range(int(len(temp_gauss_result)/mc_flag/3)):
                # warn that the width used to separate narrow from broad is not exact 1200 km s-1 which would lead to wrong judgement
                if self.CalFWHM(temp_gauss_result[(2+p*3)*mc_flag]) < 1200.:
                    color = 'g'
                else:
                    color = 'r'
                
                line_single = self.Onegauss(np.log(wave), temp_gauss_result[p*3*mc_flag:(p+1)*3*mc_flag:mc_flag])
                
                ax.plot(wave, line_single+f_conti_model, color=color, zorder=5)
                for c in range(self.ncomp):
                    axn[1][c].plot(wave, line_single, color=color, zorder=line_order[color])
                lines_total += line_single
            
            ax.plot(wave, lines_total+f_conti_model, 'b', label='line',
                    zorder=6)  # supplement the emission lines in the firs subplot
            for c in range(self.ncomp):
                tname = texlinename(uniq_linecomp_sort[c])
                axn[1][c].plot(wave, lines_total, color='b', zorder=10)
                axn[1][c].plot(wave, self.line_flux, 'k', zorder=0)
                
                axn[1][c].set_xlim(all_comp_range[2*c:2*c+2])
                f_max = line_flux[
                    np.where((wave > all_comp_range[2*c]) & (wave < all_comp_range[2*c+1]), True, False)].max()
                f_min = line_flux[
                    np.where((wave > all_comp_range[2*c]) & (wave < all_comp_range[2*c+1]), True, False)].min()
                axn[1][c].set_ylim(f_min*0.9, f_max*1.1)
                axn[1][c].set_xticks([all_comp_range[2*c], np.round((all_comp_range[2*c]+all_comp_range[2*c+1])/2, -1),
                                      all_comp_range[2*c+1]])
                axn[1][c].text(0.02, 0.9, tname, fontsize=20, transform=axn[1][c].transAxes)
                axn[1][c].text(0.02, 0.80, r'$\chi ^2_r=$'+str(np.round(float(self.comp_result[c*6+3]), 2)),
                               fontsize=16, transform=axn[1][c].transAxes)
        else:
            fig, ax = plt.subplots(nrows=1, ncols=1,
                                   figsize=(15, 8))  # if no lines are fitted, there would be only one row
        
        if self.ra == -999. or self.dec == -999.:
            ax.set_title(str(self.sdss_name)+'   z = '+str(np.round(z, 4)), fontsize=20)
        else:
            ax.set_title('ra,dec = ('+str(ra)+','+str(dec)+')   '+str(self.sdss_name)+'   z = '+str(np.round(z, 4)),
                         fontsize=20)
        
        ax.plot(self.wave_prereduced, self.flux_prereduced, 'k', label='data', zorder=2)
        
        if decomposition_host == True and self.decomposed == True:
            ax.plot(wave, self.qso+self.host, 'pink', label='host+qso temp', zorder=3)
            ax.plot(wave, flux, 'grey', label='data-host', zorder=1)
            ax.plot(wave, self.host, 'purple', label='host', zorder=4)
        else:
            host = self.flux_prereduced.min()
        
        ax.scatter(wave[tmp_all], np.repeat(self.flux_prereduced.max()*1.05, len(wave[tmp_all])), color='grey',
                   marker='o')  # plot continuum region
        
        ax.plot([0, 0], [0, 0], 'r', label='line br', zorder=5)
        ax.plot([0, 0], [0, 0], 'g', label='line na', zorder=5)
        ax.plot(wave, f_conti_model, 'c', lw=2, label='FeII', zorder=7)
        if self.BC == True:
            ax.plot(wave, self.f_pl_model+self.f_poly_model+self.f_bc_model, 'y', lw=2, label='BC', zorder=8)
        ax.plot(wave,
                conti_fit.params[6]*(wave/3000.0)**conti_fit.params[7]+self.F_poly_conti(wave, conti_fit.params[11:]),
                color='orange', lw=2, label='conti', zorder=9)
        if self.decomposed == False:
            plot_bottom = flux.min()
        else:
            plot_bottom = min(self.host.min(), flux.min())
        
        ax.set_ylim(plot_bottom*0.9, self.flux_prereduced.max()*1.1)
        
        if self.plot_legend == True:
            ax.legend(loc='best', frameon=False, ncol=2, fontsize=10)
        
        # plot line name--------
        if self.plot_line_name == True:
            line_cen = np.array(
                [6564.60, 6549.85, 6585.27, 6718.29, 6732.66, 4862.68, 5008.24, 4687.02, 4341.68, 3934.78, 3728.47,
                 3426.84, 2798.75, 1908.72, 1816.97, 1750.26, 1718.55, 1549.06, 1640.42, 1402.06, 1396.76, 1335.30, \
                 1215.67])
            
            line_name = np.array(
                ['', '', r'H$\alpha$+[NII]', '', '[SII]6718,6732', r'H$\beta$', '[OIII]', 'HeII4687', r'H$\gamma$', 
                 'CaII3934', '[OII]3728',
                 'NeV3426', 'MgII', 'CIII]', 'SiII1816', 'NIII]1750', 'NIV]1718', 'CIV', 'HeII1640', '', 'SiIV+OIV',
                 'CII1335', r'Ly$\alpha$'])
            
            for ll in range(len(line_cen)):
                if wave.min() < line_cen[ll] < wave.max():
                    ax.plot([line_cen[ll], line_cen[ll]], [plot_bottom*0.9, self.flux_prereduced.max()*1.1], 'k:')
                    ax.text(line_cen[ll]+7, 1.08*self.flux_prereduced.max(), line_name[ll], rotation=90, fontsize=10,
                            va='top')
        
        ax.set_xlim(wave.min(), wave.max())
        
        if linefit == True:
            ax.text(0.5, -1.4, r'$\rm Rest \, Wavelength$ ($\rm \AA$)', fontsize=20, transform=ax.transAxes,
                    ha='center')
            ax.text(-0.1, -0.1, r'$\rm f_{\lambda}$ ($\rm 10^{-17} erg\;s^{-1}\;cm^{-2}\;\AA^{-1}$)', fontsize=20,
                    transform=ax.transAxes, rotation=90, ha='center', rotation_mode='anchor')
        else:
            plt.xlabel(r'$\rm Rest \, Wavelength$ ($\rm \AA$)', fontsize=20)
            plt.ylabel(r'$\rm f_{\lambda}$ ($\rm 10^{-17} erg\;s^{-1}\;cm^{-2}\;\AA^{-1}$)', fontsize=20)
        
        if self.save_fig == True:
            plt.savefig(save_fig_path+self.sdss_name+'.pdf')
        plt.show()
        plt.close()
    

    # line function-----------
    def _DoLineFit(self, wave, line_flux, err, f):
        """Fit the emission lines with Gaussian profile """
        
        # remove abosorbtion line in emission line region
        # remove the pixels below continuum 
        ind_neg_line = ~np.where(((((wave > 2700.) & (wave < 2900.)) | ((wave > 1700.) & (wave < 1970.)) | (
                (wave > 1500.) & (wave < 1700.)) | ((wave > 1290.) & (wave < 1450.)) | (
                                           (wave > 1150.) & (wave < 1290.))) & (line_flux < -err)), True, False)
        
        # read line parameter
        linepara = fits.open(self.path+'qsopar.fits')
        linelist = linepara[1].data
        self.linelist = linelist
        
        ind_kind_line = np.where((linelist['lambda'] > wave.min()) & (linelist['lambda'] < wave.max()), True, False)
        if ind_kind_line.any() == True:
            # sort complex name with line wavelength
            uniq_linecomp, uniq_ind = np.unique(linelist['compname'][ind_kind_line], return_index=True)
            uniq_linecomp_sort = uniq_linecomp[linelist['lambda'][ind_kind_line][uniq_ind].argsort()]
            ncomp = len(uniq_linecomp_sort)
            compname = linelist['compname']
            allcompcenter = np.sort(linelist['lambda'][ind_kind_line][uniq_ind])
            
            # loop over each complex and fit n lines simutaneously
            
            comp_result = np.array([])
            comp_result_type = np.array([])
            comp_result_name = np.array([])
            gauss_result = np.array([])
            gauss_result_type = np.array([])
            gauss_result_name = np.array([])
            all_comp_range = np.array([])
            fur_result = np.array([])
            fur_result_type = np.array([])
            fur_result_name = np.array([])
            self.na_all_dict = {}
            
            for ii in range(ncomp):
                compcenter = allcompcenter[ii]
                ind_line = np.where(linelist['compname'] == uniq_linecomp_sort[ii], True, False)  # get line index
                linecompname = uniq_linecomp_sort[ii]
                nline_fit = np.sum(ind_line)  # n line in one complex
                linelist_fit = linelist[ind_line]
                # n gauss in each line
                ngauss_fit = np.asarray(linelist_fit['ngauss'], dtype=int)
                
                # for iitmp in range(nline_fit):   # line fit together
                comp_range = [linelist_fit[0]['minwav'], linelist_fit[0]['maxwav']]  # read complex range from table
                all_comp_range = np.concatenate([all_comp_range, comp_range])
                
                # ----tie lines--------
                self._do_tie_line(linelist, ind_line)
                
                # get the pixel index in complex region and remove negtive abs in line region
                ind_n = np.where((wave > comp_range[0]) & (wave < comp_range[1]) & (ind_neg_line == True), True, False)
                
                if np.sum(ind_n) > 10:
                    # call kmpfit for lines
                    
                    line_fit = self._do_line_kmpfit(linelist, line_flux, ind_line, ind_n, nline_fit, ngauss_fit)
                    
                    # calculate MC err
                    if self.MC == True and self.n_trails > 0:
                        all_para_std, fwhm_std, sigma_std, ew_std, peak_std, area_std, na_dict = self.new_line_mc(
                            np.log(wave[ind_n]), line_flux[ind_n], err[ind_n], self.line_fit_ini, self.line_fit_par,
                            self.n_trails, compcenter, linecompname, ind_line, nline_fit, linelist_fit, ngauss_fit)
                        self.na_all_dict.update(na_dict)
                    
                    # ----------------------get line fitting results----------------------
                    # complex parameters
                    
                    # tie lines would reduce the number of parameters increasing the dof
                    dof_fix = 0
                    if self.tie_lambda == True:
                        dof_fix += np.max((len(self.ind_tie_vindex1), 1))-1
                        dof_fix += np.max((len(self.ind_tie_vindex2), 1))-1
                    if self.tie_width == True:
                        dof_fix += np.max((len(self.ind_tie_windex1), 1))-1
                        dof_fix += np.max((len(self.ind_tie_windex2), 1))-1
                    if self.tie_flux_1 == True:
                        dof_fix += np.max((len(self.ind_tie_findex1), 1))-1
                        dof_fix += np.max((len(self.ind_tie_findex2), 1))-1
                    
                    comp_result_tmp = np.array(
                        [[linelist['compname'][ind_line][0]], [line_fit.status], [line_fit.chi2_min],
                         [line_fit.chi2_min/(line_fit.dof+dof_fix)], [line_fit.niter],
                         [line_fit.dof+dof_fix]]).flatten()
                    comp_result_type_tmp = np.array(['str', 'int', 'float', 'float', 'int', 'int'])
                    comp_result_name_tmp = np.array(
                        [str(ii+1)+'_complex_name', str(ii+1)+'_line_status', str(ii+1)+'_line_min_chi2',
                         str(ii+1)+'_line_red_chi2', str(ii+1)+'_niter', str(ii+1)+'_ndof'])
                    comp_result = np.concatenate([comp_result, comp_result_tmp])
                    comp_result_name = np.concatenate([comp_result_name, comp_result_name_tmp])
                    comp_result_type = np.concatenate([comp_result_type, comp_result_type_tmp])
                    
                    # gauss result -------------
                    
                    gauss_tmp = np.array([])
                    gauss_type_tmp = np.array([])
                    gauss_name_tmp = np.array([])
                    
                    for gg in range(len(line_fit.params)):
                        gauss_tmp = np.concatenate([gauss_tmp, np.array([line_fit.params[gg]])])
                        if self.MC == True and self.n_trails > 0:
                            gauss_tmp = np.concatenate([gauss_tmp, np.array([all_para_std[gg]])])
                    gauss_result = np.concatenate([gauss_result, gauss_tmp])
                    
                    # gauss result name -----------------
                    for n in range(nline_fit):
                        for nn in range(int(ngauss_fit[n])):
                            line_name = linelist['linename'][ind_line][n]+'_'+str(nn+1)
                            if self.MC == True and self.n_trails > 0:
                                gauss_type_tmp_tmp = ['float', 'float', 'float', 'float', 'float', 'float']
                                gauss_name_tmp_tmp = [line_name+'_scale', line_name+'_scale_err',
                                                      line_name+'_centerwave', line_name+'_centerwave_err',
                                                      line_name+'_sigma', line_name+'_sigma_err']
                            else:
                                gauss_type_tmp_tmp = ['float', 'float', 'float']
                                gauss_name_tmp_tmp = [line_name+'_scale', line_name+'_centerwave', line_name+'_sigma']
                            gauss_name_tmp = np.concatenate([gauss_name_tmp, gauss_name_tmp_tmp])
                            gauss_type_tmp = np.concatenate([gauss_type_tmp, gauss_type_tmp_tmp])
                    gauss_result_type = np.concatenate([gauss_result_type, gauss_type_tmp])
                    gauss_result_name = np.concatenate([gauss_result_name, gauss_name_tmp])
                    
                    # further line parameters ----------
                    fur_result_tmp = np.array([])
                    fur_result_type_tmp = np.array([])
                    fur_result_name_tmp = np.array([])
                    fwhm, sigma, ew, peak, area = self.line_prop(compcenter, line_fit.params, 'broad')
                    br_name = uniq_linecomp_sort[ii]
                    
                    if self.MC == True and self.n_trails > 0:
                        fur_result_tmp = np.array(
                            [fwhm, fwhm_std, sigma, sigma_std, ew, ew_std, peak, peak_std, area, area_std])
                        fur_result_type_tmp = np.concatenate([fur_result_type_tmp,
                                                              ['float', 'float', 'float', 'float', 'float', 'float',
                                                               'float', 'float', 'float', 'float']])
                        fur_result_name_tmp = np.array(
                            [br_name+'_whole_br_fwhm', br_name+'_whole_br_fwhm_err', br_name+'_whole_br_sigma',
                             br_name+'_whole_br_sigma_err', br_name+'_whole_br_ew', br_name+'_whole_br_ew_err',
                             br_name+'_whole_br_peak', br_name+'_whole_br_peak_err', br_name+'_whole_br_area',
                             br_name+'_whole_br_area_err'])
                    else:
                        fur_result_tmp = np.array([fwhm, sigma, ew, peak, area])
                        fur_result_type_tmp = np.concatenate(
                            [fur_result_type_tmp, ['float', 'float', 'float', 'float', 'float']])
                        fur_result_name_tmp = np.array(
                            [br_name+'_whole_br_fwhm', br_name+'_whole_br_sigma', br_name+'_whole_br_ew',
                             br_name+'_whole_br_peak', br_name+'_whole_br_area'])
                    fur_result = np.concatenate([fur_result, fur_result_tmp])
                    fur_result_type = np.concatenate([fur_result_type, fur_result_type_tmp])
                    fur_result_name = np.concatenate([fur_result_name, fur_result_name_tmp])
                
                else:
                    print("less than 10 pixels in line fitting!")
            
            line_result = np.concatenate([comp_result, gauss_result, fur_result])
            line_result_type = np.concatenate([comp_result_type, gauss_result_type, fur_result_type])
            line_result_name = np.concatenate([comp_result_name, gauss_result_name, fur_result_name])
        
        else:
            line_result = np.array([])
            line_result_name = np.array([])
            comp_result = np.array([])
            gauss_result = np.array([])
            gauss_result_name = np.array([])
            line_result_type = np.array([])
            ncomp = 0
            all_comp_range = np.array([])
            uniq_linecomp_sort = np.array([])
            print("No line to fit! Pleasse set Line_fit to FALSE or enlarge wave_range!")
        
        self.comp_result = comp_result
        self.gauss_result = gauss_result
        self.gauss_result_name = gauss_result_name
        self.line_result = line_result
        self.line_result_type = line_result_type
        self.line_result_name = line_result_name
        self.ncomp = ncomp
        self.line_flux = line_flux
        self.all_comp_range = all_comp_range
        self.uniq_linecomp_sort = uniq_linecomp_sort
        return self.line_result, self.line_result_name


    # ---------MC error for emission line parameters-------------------
    def new_line_mc(self, x, y, err, pp0, pp_limits, n_trails, compcenter, linecompname,
                    ind_line, nline_fit, linelist_fit, ngauss_fit):
        """calculate the Monte Carlo errror of line parameters"""
        linelist = self.linelist
        linenames = linelist[linelist['compname']==linecompname]['linename']
        all_para_1comp = np.zeros(len(pp0)*n_trails).reshape(len(pp0), n_trails)
        all_para_std = np.zeros(len(pp0))
        all_fwhm = np.zeros(n_trails)
        all_sigma = np.zeros(n_trails)
        all_ew = np.zeros(n_trails)
        all_peak = np.zeros(n_trails)
        all_area = np.zeros(n_trails)
        na_all_dict = {}
        for line in linenames: 
            if 'br' not in line and 'na' not in line:
                emp_dict = {'fwhm': [],
                            'sigma' : [],
                            'ew' : [],
                            'peak' : [],
                            'area' : []}
                na_all_dict.setdefault(line, emp_dict)

        for tra in range(n_trails):
            flux = y+np.random.randn(len(y))*err
            line_fit = kmpfit.Fitter(residuals=self._residuals_line, data=(x, flux, err), maxiter=50)
            line_fit.parinfo = pp_limits
            line_fit.fit(params0=pp0)
            line_fit.params = self.newpp
            all_para_1comp[:, tra] = line_fit.params
            
            # further line properties
            all_fwhm[tra], all_sigma[tra], all_ew[tra], all_peak[tra], all_area[tra] 
            broad_all = self.line_prop(compcenter, line_fit.params, 'broad')
            all_fwhm[tra] = broad_all[0]
            all_sigma[tra] =  broad_all[1]
            all_ew[tra] = broad_all[2]
            all_peak[tra] = broad_all[3]
            all_area[tra] = broad_all[4]     
            all_line_name = []
            for n in range(nline_fit):
                for nn in range(int(ngauss_fit[n])):
                    # line_name = linelist['linename'][ind_line][n]+'_'+str(nn+1)
                    line_name = linelist['linename'][ind_line][n]
                    # print(line_name)
                    all_line_name.append(line_name)
            all_line_name = np.asarray(all_line_name)

            for line in linenames: 
                if 'br' not in line and 'na' not in line:
                    try:
                        par_ind = np.where(all_line_name==line)[0][0]*3
                        linecenter = linelist[linelist['linename']==line]['lambda']
                        na_tmp = self.line_prop(linecenter, line_fit.params[par_ind:par_ind+3], 'narrow')
                        # print(line+'params: {}'.format(line_fit.params))
                        # print('The index of the param is {}'.format(par_ind))
                        na_all_dict[line]['fwhm'].append(na_tmp[0])
                        na_all_dict[line]['sigma'].append(na_tmp[1])
                        na_all_dict[line]['ew'].append(na_tmp[2])
                        na_all_dict[line]['peak'].append(na_tmp[3])
                        na_all_dict[line]['area'].append(na_tmp[4])
                    except:
                        print('Mismatch.')
                        pass
                    
        for line in linenames: 
            if 'br' not in line and 'na' not in line:
                na_all_dict[line]['fwhm'] = np.asarray(na_all_dict[line]['fwhm']).flatten()
                na_all_dict[line]['sigma'] = np.asarray(na_all_dict[line]['sigma']).flatten()
                na_all_dict[line]['ew'] = np.asarray(na_all_dict[line]['ew']).flatten()
                na_all_dict[line]['peak'] = np.asarray(na_all_dict[line]['peak']).flatten()
                na_all_dict[line]['area'] = np.asarray(na_all_dict[line]['area']).flatten()
       
        for st in range(len(pp0)):
            all_para_std[st] = all_para_1comp[st, :].std()
        
        return all_para_std, all_fwhm.std(), all_sigma.std(), all_ew.std(), all_peak.std(), all_area.std(), na_all_dict



# Return LaTeX name for a line / complex name
def texlinename(name):
    if name == 'Ha':
        tname = r'H$\alpha$'
    elif name == 'Hb':
        tname = r'H$\beta$'
    elif name == 'Hr':
        tname = r'H$\gamma$'
    elif name == 'Hg':
        tname = r'H$\gamma$'
    elif name == 'Lya':
        tname = r'Ly$\alpha$'
    else:
        tname = name
    return tname