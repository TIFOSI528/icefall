import torch
from torch import Tensor
from torch import nn
import math
import random
from typing import Tuple, List


class TensorDiagnosticOptions(object):
    """
    Options object for tensor diagnostics:

     Args:
        memory_limit: the maximum number of bytes we store per tensor (limits how many copies
                of the tensor we cache).
        max_eig_dim: the maximum dimension for which we print out eigenvalues
               (limited for speed reasons).
    """
    def __init__(self,
                 memory_limit: int = (2 ** 20),
                 max_eig_dim: int = 512):

        self.memory_limit = memory_limit
        self.max_eig_dim = max_eig_dim

    def dim_is_summarized(self, size: int):
        return size > 10 and size != 31



def get_tensor_stats(x: Tensor, dim: int,
                      stats_type: str) -> Tuple[Tensor, int]:
    """
    Returns the specified transformation of the Tensor (either x or x.abs()
    or (x > 0), summed over all but the index `dim`.

    Args:
       x: Tensor, tensor to be analyzed
      dim: dimension with 0 <= dim < x.ndim
      stats_type:
          "abs" -> take abs() before summing
          "positive" -> take (x > 0) before summing
          "rms" -> square before summing, we'll take sqrt later
          "value -> just sum x itself
    Returns (stats, count)
       where stats is a Tensor of shape (x.shape[dim],), and the count
       is an integer saying how many items were counted in each element
       of stats.
    """
    count = x.numel() // x.shape[dim]

    if stats_type == "eigs":
        x = x.transpose(dim, -1)
        x = x.reshape(-1, x.shape[-1])
        # shape of returned tensor: (s, s) where s is size of dimension `dim` of original x.
        return torch.matmul(x.transpose(0, 1), x), count
    elif stats_type == "abs":
        x = x.abs()
    elif stats_type == "rms":
        x = x ** 2
    elif stats_type == "positive":
        x = (x > 0).to(dtype=torch.float)
    else:
        assert stats_type == "value"

    sum_dims = [ d for d in range(x.ndim) if d != dim ]
    if len(sum_dims) > 0:
        x = torch.sum(x, dim=sum_dims)
    x = x.flatten()
    return x, count

def get_diagnostics_for_dim(dim: int, tensors: List[Tensor],
                            options: TensorDiagnosticOptions,
                            sizes_same: bool,
                            stats_type: str):
    """
    This function gets diagnostics for a dimension of a module.
    Args:
           dim: the dimension to analyze, with 0 <= dim < tensors[0].ndim
       options: options object
    sizes_same: true if all the tensor sizes are the same on this dimension
    stats_type: either "abs" or "positive" or "eigs" or "value",
               imdictates the type of stats
               we accumulate, abs is mean absolute value, "positive"
               is proportion of positive to nonnegative values, "eigs"
               is eigenvalues after doing outer product on this dim, sum
               over all other dimes.
    Returns:
     Diagnostic as a string, either percentiles or the actual values,
     see the code.  Will return the empty string if the diagnostics did
     not make sense to print out for this dimension, e.g. dimension
     mismatch and stats_type == "eigs"
    """
    # stats_and_counts is a list of pair (Tensor, int)
    stats_and_counts = [ get_tensor_stats(x, dim, stats_type) for x in tensors ]
    stats = [ x[0] for x in stats_and_counts ]
    counts = [ x[1] for x in stats_and_counts ]

    if stats_type == "eigs":
        try:
            stats = torch.stack(stats).sum(dim=0)
        except:
            return ''
        count = sum(counts)
        stats = stats / count
        stats, _ = torch.symeig(stats)
        stats = stats.abs().sqrt()  # sqrt so it reflects data magnitude, like stddev- not variance
    elif sizes_same:
        stats = torch.stack(stats).sum(dim=0)
        count = sum(counts)
        stats = stats / count
    else:
        stats = [ x[0] / x[1] for x in stats_and_counts ]
        stats = torch.cat(stats, dim=0)
    if stats_type == 'rms':
        stats = stats.sqrt()

    # if `summarize` we print percentiles of the stats; else,
    # we print out individual elements.
    summarize = (not sizes_same) or options.dim_is_summarized(stats.numel())
    if summarize:
        # print out percentiles.
        stats = stats.sort()[0]
        num_percentiles = 10
        size = stats.numel()
        percentiles = []
        for i in range(num_percentiles + 1):
            index = (i * (size - 1)) // num_percentiles
            percentiles.append(stats[index].item())
        percentiles = [ '%.2g' % x for x in percentiles ]
        percentiles = ' '.join(percentiles)
        ans = f'percentiles: [{percentiles}]'
    else:
        ans = stats.tolist()
        ans = [ '%.2g' % x for x in ans ]
        ans = '[' + ' '.join(ans) + ']'
    if stats_type == "value":
        # This norm is useful because it is strictly less than the largest
        # sqrt(eigenvalue) of the variance, which we print out, and shows,
        # speaking in an approximate way, how much of that largest eigenvalue
        # can be attributed to the mean of the distribution.
        norm = (stats ** 2).sum().sqrt().item()
        mean = stats.mean().item()
        rms = (stats ** 2).mean().sqrt().item()
        ans += f', norm={norm:.2g}, mean={mean:.2g}, rms={rms:.2g}'
    else:
        mean = stats.mean().item()
        rms = (stats ** 2).mean().sqrt().item()
        ans += f', mean={mean:.2g}, rms={rms:.2g}'
    return ans



def print_diagnostics_for_dim(name: str, dim: int, tensors: List[Tensor],
                              options: TensorDiagnosticOptions):
    ndim = tensors[0].ndim
    if ndim > 1:
        stats_types = ["abs", "positive", "value", "rms"]
        if tensors[0].shape[dim] <= options.max_eig_dim:
            stats_types.append("eigs")
    else:
        stats_types = [ "value", "abs" ]

    for stats_type in stats_types:
        sizes = [ x.shape[dim] for x in tensors ]
        sizes_same = all([ x == sizes[0] for x in sizes ])
        s = get_diagnostics_for_dim(dim, tensors,
                                    options, sizes_same,
                                    stats_type)
        if s == '':
            continue

        min_size = min(sizes)
        max_size = max(sizes)
        size_str = f"{min_size}" if sizes_same else f"{min_size}..{max_size}"
        # stats_type will be "abs" or "positive".
        print(f"module={name}, dim={dim}, size={size_str}, {stats_type} {s}")


class TensorDiagnostic(object):
    """
    This class is not directly used by the user, it is responsible for collecting
    diagnostics for a single parameter tensor of a torch.Module.
    """
    def __init__(self,
                 opts: TensorDiagnosticOptions,
                 name: str):
        self.name = name
        self.opts = opts
        self.saved_tensors = []

    def accumulate(self, x):
        if isinstance(x, Tuple):
            x = x[0]
        if not isinstance(x, Tensor):
            return
        if x.device == torch.device('cpu'):
            x = x.detach().clone()
        else:
            x = x.detach().to('cpu', non_blocking=True)
        self.saved_tensors.append(x)
        l = len(self.saved_tensors)
        if l & (l - 1) == 0: # power of 2..
            self._limit_memory()

    def _limit_memory(self):
        if len(self.saved_tensors) > 1024:
            self.saved_tensors = self.saved_tensors[-1024:]
            return

        tot_mem = 0.0
        for i in reversed(range(len(self.saved_tensors))):
            tot_mem += self.saved_tensors[i].numel() * self.saved_tensors[i].element_size()
            if tot_mem > self.opts.memory_limit:
                self.saved_tensors = self.saved_tensors[i:]
                return

    def print_diagnostics(self):
        if len(self.saved_tensors) == 0:
            print("{name}: no stats".format(name=self.name))
            return
        if self.saved_tensors[0].ndim == 0:
            # ensure there is at least one dim.
            self.saved_tensors = [ x.unsqueeze(0) for x in self.saved_tensors ]

        try:
            device = torch.device('cuda')
            torch.ones(1, 1, device)
        except:
            device = torch.device('cpu')

        ndim = self.saved_tensors[0].ndim
        tensors = [x.to(device) for x in self.saved_tensors]
        for dim in range(ndim):
            print_diagnostics_for_dim(self.name, dim,
                                      tensors,
                                      self.opts)


class ModelDiagnostic(object):
    def __init__(self, opts: TensorDiagnosticOptions = TensorDiagnosticOptions()):
        self.diagnostics = dict()
        self.opts = opts

    def __getitem__(self, name: str):
        if name not in self.diagnostics:
            self.diagnostics[name] = TensorDiagnostic(self.opts, name)
        return self.diagnostics[name]

    def print_diagnostics(self):
        for k in sorted(self.diagnostics.keys()):
            self.diagnostics[k].print_diagnostics()



def attach_diagnostics(model: nn.Module,
                       opts: TensorDiagnosticOptions) -> ModelDiagnostic:
    ans = ModelDiagnostic(opts)
    for name, module in model.named_modules():
        if name == '':
            name = "<top-level>"
        forward_diagnostic = TensorDiagnostic(opts, name + ".output")
        backward_diagnostic = TensorDiagnostic(opts, name + ".grad")


        # setting model_diagnostic=ans and n=name below, instead of trying to capture the variables,
        # ensures that we use the current values.  (matters for name, since
        # the variable gets overwritten).  these closures don't really capture
        # by value, only by "the final value the variable got in the function" :-(
        def forward_hook(_module, _input, _output,
                         _model_diagnostic=ans, _name=name):
            if isinstance(_output, Tensor):
                _model_diagnostic[f"{_name}.output"].accumulate(_output)
            elif isinstance(_output, tuple):
                for i, o in enumerate(_output):
                    _model_diagnostic[f"{_name}.output[{i}]"].accumulate(o)

        def backward_hook(_module, _input, _output,
                          _model_diagnostic=ans, _name=name):
            if isinstance(_output, Tensor):
                _model_diagnostic[f"{_name}.grad"].accumulate(_output)
            elif isinstance(_output, tuple):
                for i, o in enumerate(_output):
                    _model_diagnostic[f"{_name}.grad[{i}]"].accumulate(o)

        module.register_forward_hook(forward_hook)
        module.register_backward_hook(backward_hook)

    for name, parameter in model.named_parameters():

        def param_backward_hook(grad,
                                _parameter=parameter,
                                _model_diagnostic=ans,
                                _name=name):
            _model_diagnostic[f"{_name}.param_value"].accumulate(_parameter)
            _model_diagnostic[f"{_name}.param_grad"].accumulate(grad)

        parameter.register_hook(param_backward_hook)
    return ans



def _test_tensor_diagnostic():
    opts = TensorDiagnosticOptions(2**20, 512)

    diagnostic = TensorDiagnostic(opts, "foo")

    for _ in range(10):
        diagnostic.accumulate(torch.randn(50, 100) * 10.0)

    diagnostic.print_diagnostics()

    model = nn.Sequential(nn.Linear(100, 50), nn.Linear(50, 80))

    diagnostic = attach_diagnostics(model, opts)
    for _ in range(10):
        T = random.randint(200, 300)
        x = torch.randn(T, 100)
        y = model(x)
        y.sum().backward()

    diagnostic.print_diagnostics()



if __name__ == '__main__':
    _test_tensor_diagnostic()


def _test_func():
    ans = []
    for i in range(10):
        x = list()
        x.append(i)
        def func():
            return x
        ans.append(func)
    return ans
