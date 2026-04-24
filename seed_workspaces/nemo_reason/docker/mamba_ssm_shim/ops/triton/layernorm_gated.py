"""Pure-torch fallback for rmsnorm_fn used by Nemotron-H modeling code.

The fused kernel combines gated RMSNorm: y = ((x * gate.sigmoid()) / rms(x*gate.sigmoid())) * weight.
But actually inspecting the call sites, it's a gated GeGLU variant: norm_(x) * silu(z) or similar.
We provide a compatible signature that covers the Nemotron-H usage.
"""
import torch
import torch.nn.functional as F


def _rms_norm(x, weight, eps):
    dtype = x.dtype
    x = x.float()
    variance = x.pow(2).mean(-1, keepdim=True)
    x = x * torch.rsqrt(variance + eps)
    if weight is not None:
        x = x * weight.float()
    return x.to(dtype)


def rmsnorm_fn(x, weight, bias=None, z=None, eps=1e-6, group_size=None,
               norm_before_gate=True, is_rms_norm=True):
    """Fallback matching mamba_ssm.ops.triton.layernorm_gated.rmsnorm_fn signature."""
    if group_size is not None and group_size > 0 and group_size != x.shape[-1]:
        # group-norm variant: split last dim into groups
        B = x.shape[:-1]
        D = x.shape[-1]
        assert D % group_size == 0
        ngroups = D // group_size
        x = x.reshape(*B, ngroups, group_size)
        if z is not None:
            z = z.reshape(*B, ngroups, group_size)
        if weight is not None:
            w = weight.reshape(ngroups, group_size)
        else:
            w = None
        if bias is not None:
            b = bias.reshape(ngroups, group_size)
        else:
            b = None
    else:
        w = weight
        b = bias
        ngroups = None

    if z is not None and not norm_before_gate:
        # apply gate via silu (matches mamba usage) then norm
        x = x * F.silu(z.to(x.dtype))
        out = _rms_norm(x, w, eps)
        if b is not None:
            out = out + b
    else:
        out = _rms_norm(x, w, eps)
        if b is not None:
            out = out + b
        if z is not None:
            out = out * F.silu(z.to(out.dtype))

    if ngroups is not None:
        out = out.reshape(*B, D)
    return out


class RMSNormGated(torch.nn.Module):
    def __init__(self, dim, eps=1e-6, group_size=None, norm_before_gate=True):
        super().__init__()
        self.weight = torch.nn.Parameter(torch.ones(dim))
        self.eps = eps
        self.group_size = group_size
        self.norm_before_gate = norm_before_gate

    def forward(self, x, z=None):
        return rmsnorm_fn(x, self.weight, bias=None, z=z, eps=self.eps,
                          group_size=self.group_size,
                          norm_before_gate=self.norm_before_gate)
