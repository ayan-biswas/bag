# BSD 3-Clause License
#
# Copyright (c) 2018, Regents of the University of California
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import re
from typing import Mapping, BinaryIO, Any, Dict, Sequence
from pathlib import Path
import numpy as np

from pybag.enum import DesignOutput

from .data import AnalysisData, SimData, _check_is_md
from .srr import combine_ana_sim_envs


class NutBinParser:
    def __init__(self, raw_path: Path, rtol: float, atol: float, monte_carlo: bool) -> None:
        self._cwd_path = raw_path.parent
        self._monte_carlo = monte_carlo
        nb_data = self.parse_raw_file(raw_path)
        self._sim_data = self.convert_to_sim_data(nb_data, rtol, atol)

    @property
    def sim_data(self) -> SimData:
        return self._sim_data

    @property
    def monte_carlo(self) -> bool:
        return self._monte_carlo

    def parse_raw_file(self, raw_path: Path) -> Mapping[str, Any]:
        with open(raw_path, 'rb') as f:
            f.readline()    # skip title
            f.readline()    # skip date

            # read all the individual analyses
            ana_dict = {}
            while True:
                # read Plotname or EOF
                plotname = f.readline().decode('ascii')
                if len(plotname) == 0:  # EOF
                    break
                data = self.parse_analysis(f)
                self.populate_dict(ana_dict, plotname, data)
        return ana_dict

    @staticmethod
    def parse_analysis(f: BinaryIO) -> Dict[str, np.ndarray]:
        # read flags
        flags = f.readline().decode('ascii').split()
        if flags[1] == 'real':
            nptype = float
        elif flags[1] == 'complex':
            nptype = complex
        else:
            raise ValueError(f'Flag type {flags[1]} is not recognized.')

        # read number of variables and points
        num_vars = int(f.readline().decode('ascii').split()[-1])
        num_points = int(f.readline().decode('ascii').split()[-1])

        # get the variable names, ignore units and other flags
        var_names = []
        for idx in range(num_vars):
            _line = f.readline().decode('ascii').split()
            if idx == 0:
                var_names.append(_line[2])
            else:
                var_names.append(_line[1])

        f.readline()    # skip "Binary:"
        # read big endian binary data
        bin_data = np.fromfile(f, dtype=np.dtype(nptype).newbyteorder(">"), count=num_vars * num_points)
        data = {}
        for idx, var_name in enumerate(var_names):
            if var_name == 'freq':
                data[var_name] = np.real(bin_data[idx::num_vars])
            else:
                data[var_name] = bin_data[idx::num_vars]

        return data

    @staticmethod
    def get_info_from_plotname(plotname: str) -> Mapping[str, Any]:
        # TODO: pnoise
        # get ana_name from plotname
        ana_name = re.search('`.*\'', plotname).group(0)[1:-1]

        # get ana_type and sim_env from ana_name
        ana_type_fmt = '[a-zA-Z]+'
        sim_env_fmt = '[a-zA-Z0-9]+_[a-zA-Z0-9]+'
        m = re.search(f'({ana_type_fmt})__({sim_env_fmt})', ana_name)
        ana_type = m.group(1)

        # get inner sweep information from plotname, if any
        m_in = re.search(r': (\w+) =', plotname)
        if m_in is not None:
            inner_sweep = m_in.group(1)
        else:
            inner_sweep = ''
        if ana_type == 'pss':
            if inner_sweep == 'freq':
                ana_type = 'pss_fd'
            elif inner_sweep == 'time':
                ana_type = 'pss_td'
            else:
                raise NotImplementedError

        # get outer sweep information from ana_name, if any
        m_swp = re.findall('swp[0-9]{2}', ana_name)
        m_swp1 = re.findall('swp[0-9]{2}-[0-9]{3}', ana_name)
        swp_combo = []
        for idx, val in enumerate(m_swp1):
            swp_combo.append(int(val[-3:]))

        return dict(
            ana_name=ana_name,
            sim_env=m.group(2),
            ana_type=ana_type,
            swp_info=m_swp,
            swp_combo=swp_combo,
            inner_sweep=inner_sweep,
        )

    def populate_dict(self, ana_dict: Dict[str, Any], plotname: str, data: Dict[str, np.ndarray]) -> None:
        # get analysis name and sim_env
        info = self.get_info_from_plotname(plotname)
        ana_name: str = info['ana_name']
        if self.monte_carlo and not ana_name.startswith('__mc_'):
            # ignore the nominal sim in Monte Carlo
            return
        ana_type: str = info['ana_type']
        sim_env: str = info['sim_env']
        # swp_info: Sequence[str] = info['swp_info']
        swp_combo: Sequence[int] = info['swp_combo']
        inner_sweep: str = info['inner_sweep']

        if ana_type not in ana_dict:
            ana_dict[ana_type] = {}

        if sim_env not in ana_dict[ana_type]:
            # get outer sweep, if any
            # swp_vars = self.parse_sweep_info(swp_info, data, f'___{ana_type}__{sim_env}__')
            ana_dict[ana_type][sim_env] = {'data': [], 'swp_combos': [], 'inner_sweep': inner_sweep}

        ana_dict[ana_type][sim_env]['data'].append(data)
        # get outer sweep combo, if any
        if swp_combo:
            ana_dict[ana_type][sim_env]['swp_combos'].append(swp_combo)

    def parse_sweep_info(self, swp_info: Sequence[str], data: Dict[str, np.ndarray], suf: str) -> Sequence[str]:
        # read from innermost sweep outwards
        new_swp_info = list(swp_info)
        swp_vars = []
        while len(new_swp_info) > 0:
            name = '-000_'.join(new_swp_info) + suf + '.sweep'
            swp_vars.insert(0, self.parse_sweep_file(self._cwd_path / 'sim.raw.psf' / name, data))
            new_swp_info.pop()
        return swp_vars

    @staticmethod
    def parse_sweep_file(file_path: Path, data: Dict[str, np.ndarray]) -> str:
        # TODO: how to parse this?
        print('-----------------')
        print(file_path)
        with open(file_path, 'rb') as f:
            lidx = 0
            while True:
                line = f.readline()
                if len(line) == 0:  # EOF
                    break
                print(f'---- line {lidx} ---')
                try:
                    print(line.decode('ascii'))
                    print('ASCII')
                except UnicodeDecodeError:
                    print(line)
                    print('ASCII ERROR')
                lidx += 1
        print('-----------------')
        breakpoint()
        return ''

    def convert_to_sim_data(self, nb_data, rtol: float, atol: float) -> SimData:
        ana_dict = {}
        sim_envs = []
        for ana_type, sim_env_dict in nb_data.items():
            sim_envs = sorted(sim_env_dict.keys())
            sub_ana_dict = {}
            for sim_env, nb_dict in sim_env_dict.items():
                sub_ana_dict[sim_env] = self.convert_to_analysis_data(nb_dict, rtol, atol, ana_type)
            ana_dict[ana_type] = combine_ana_sim_envs(sub_ana_dict, sim_envs)
        return SimData(sim_envs, ana_dict, DesignOutput.SPECTRE)

    def convert_to_analysis_data(self, nb_dict: Mapping[str, Any], rtol: float, atol: float, ana_type: str
                                 ) -> AnalysisData:
        data = {}

        # get sweep information
        inner_sweep: str = nb_dict['inner_sweep']
        swp_combos = nb_dict['swp_combos']
        if swp_combos:  # create sweep combinations
            num_swp = len(swp_combos[0])
            swp_vars = [f'sweep{idx}' for idx in range(num_swp)]
            swp_len = len(swp_combos)
            swp_combo_list = [np.array(swp_combos)[:, i] for i in range(num_swp)]
        else:   # no outer sweep
            swp_vars = []
            swp_len = 0
            swp_combo_list = []

        # get Monte Carlo information (no parametric sweep)
        if self.monte_carlo:
            swp_vars = ['monte_carlo']
            swp_len = len(nb_dict['data'])
            swp_combo_list = [np.linspace(0, swp_len - 1, swp_len, dtype=int)]

        # if PAC, configure harmonic sweep
        if ana_type == 'pac':
            swp_vars = ['harmonic']
            # When maxsideband > 0, spectre errors with this message:
            # "ERROR (SPECTRE-7012): Output for analysis of type `pac' is not supported in Nutmeg."
            swp_len = len(nb_dict['data'])
            harmonics = swp_len // 2
            swp_combo_list = [np.linspace(-harmonics, harmonics, swp_len, dtype=int)]

        swp_shape, swp_vals = _check_is_md(1, swp_combo_list, rtol, atol, None)  # single corner per set
        is_md = swp_shape is not None
        if is_md:
            swp_combo = {var: swp_vals[i] for i, var in enumerate(swp_vars)}
        else:
            swp_combo = {var: swp_combo_list for var in swp_vars}
            swp_shape = (swp_len,)
        data.update(swp_combo)

        # parse each signal
        if swp_len == 0:    # no outer sweep
            for sig_name, sig_y in nb_dict['data'][0].items():
                data_shape = (*swp_shape, sig_y.shape[-1])
                _new_sig = sig_name.replace('/', '.')
                data[_new_sig] = sig_y if _new_sig == inner_sweep else np.reshape(sig_y, data_shape)
        else:   # combine outer sweeps
            sig_names = list(nb_dict['data'][0].keys())
            for sig_name in sig_names:
                yvecs = [nb_dict['data'][i][sig_name] for i in range(swp_len)]
                sub_dims = tuple(yvec.shape[0] for yvec in yvecs)
                max_dim = max(sub_dims)
                is_same_len = all((sub_dims[i] == sub_dims[0] for i in range(swp_len)))
                data_shape = (*swp_shape, max_dim)
                _new_sig = sig_name.replace('/', '.')
                if not is_same_len:
                    yvecs_padded = [np.pad(yvec, (0, max_dim - dim), constant_values=np.nan)
                                    for yvec, dim in zip(yvecs, sub_dims)]
                    sig_y = np.stack(yvecs_padded)
                    data[_new_sig] = np.reshape(sig_y, data_shape)
                else:
                    if _new_sig == inner_sweep:
                        data[_new_sig] = yvecs[0]
                    else:
                        sig_y = np.stack(yvecs)
                        data[_new_sig] = np.reshape(sig_y, data_shape)

        if inner_sweep:
            swp_vars.append(inner_sweep)

        return AnalysisData(['corner'] + swp_vars, data, is_md)
