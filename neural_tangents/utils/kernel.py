# Lint as: python3

# Copyright 2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""The `Kernel` class containing NTK and NNGP `np.ndarray`s as fields."""


import operator as op
import jax.numpy as np
from neural_tangents.utils import dataclasses
from neural_tangents.utils import utils
from typing import Tuple, Optional, Union


@dataclasses.dataclass
class Kernel:
  """Dataclass containing information about the NTK and NNGP of a model.

  Attributes:
    nngp: covariance between the first and second batches (NNGP). A
      `np.ndarray` of shape
      `(batch_size_1, batch_size_2, height, [height,], width, [width,], ...))`,
      where the exact shape depends on `diagonal_spatial`.
    ntk: the neural tangent kernel (NTK). `np.ndarray` of same shape as `nngp`.
    cov1: covariance of the first batch of inputs. A `np.ndarray` with shape
      `(batch_size_1, [batch_size_1,] height, [height,], width, [width,], ...)`
      where the exact shape depends on `diagonal_batch` and `diagonal_spatial`.
    cov2: optional covariance of the second batch of inputs. A `np.ndarray`
      with shape
      `(batch_size_2, [batch_size_2,] height, [height,], width, [width,], ...)`
      where the exact shape depends on `diagonal_batch` and `diagonal_spatial`.
    x1_is_x2: a boolean specifying whether `x1` and `x2` are the same.
    is_gaussian: a boolean, specifying whether the output features or channels
      of the layer / NN function (returning this `Kernel` as the `kernel_fn`)
      are i.i.d. Gaussian with covariance `nngp`, conditioned on fixed inputs to
      the layer and i.i.d. Gaussian weights and biases of the layer. For
      example, passing an input through a CNN layer with i.i.d. Gaussian weights
      and biases produces i.i.d. Gaussian random variables along the channel
      dimension, while passing an input through a nonlinearity does not.
    is_reversed: a boolean specifying whether the covariance matrices `nngp`,
      `cov1`, `cov2`, and `ntk` have the ordering of spatial dimensions
      reversed. Ignored unless `diagonal_spatial` is `False`. Used internally
      to avoid self-cancelling transpositions in a sequence of CNN layers that
      flip the order of kernel spatial dimensions.
    is_input: a boolean specifying whether the current layer is the input
      layer and it is used to avoid applying dropout to the input layer.
    diagonal_batch: a boolean specifying whether `cov1` and `cov2` store only
      the diagonal of the sample-sample covariance
      (`diagonal_batch == True`,
       `cov1.shape == (batch_size_1, ...)`),
      or the full covariance
      (`diagonal_batch == False`,
       `cov1.shape == (batch_size_1, batch_size_1, ...)`).
      Defaults to `True` as no current layers require the full covariance.
    diagonal_spatial: a boolean specifying whether all (`cov1`, `ntk`, etc.)
      covariance matrices store only the diagonals of the location-location
      covariances
      (`diagonal_spatial == True`,
       `nngp.shape == (batch_size_1, batch_size_2, height, width, depth, ...)`),
      or the full covariance
      (`diagonal_spatial == False`,
       `nngp.shape == (batch_size_1, batch_size_2, height, height,
                       width, width, depth, depth, ...)`).
      Defaults to `False`, but is set to `True` if the output top-layer
      covariance depends only on the diagonals (e.g. when a CNN network has no
      pooling layers and `Flatten` on top).
    shape1: a tuple specifying the shape of the random variable in the first
      batch of inputs. These have covariance `cov1` and covariance with the
      second batch of inputs given by `nngp`.
    shape2: a tuple specifying the shape of the random variable in the second
      batch of inputs. These have variance `cov2` and covariance with the first
      batch of inputs given by `nngp`.
    mask1: An optional boolean `np.ndarray` with a shape broadcastable to
      `shape1` (and the same number of dimensions). `True` stands for the
      input being masked at that position, while `False` means the input is
      visible. For example, if `shape1 == (5, 32, 32, 3)` (a batch of 5 `NHWC`
      CIFAR10 images), a `mask1` of shape `(5, 1, 32, 1)` means different
      images can have different blocked columns (`H` and `C` dimensions are
      always either both blocked or unblocked). `None` means no masking.
    mask2: same as `mask1`, but for the second batch of inputs.
  """

  nngp: np.ndarray
  ntk: Optional[np.ndarray]

  cov1: np.ndarray
  cov2: np.ndarray
  x1_is_x2: np.ndarray

  is_gaussian: bool = dataclasses.field(pytree_node=False)
  is_reversed: bool = dataclasses.field(pytree_node=False)
  is_input: bool = dataclasses.field(pytree_node=False)

  diagonal_batch: bool = dataclasses.field(pytree_node=False)
  diagonal_spatial: bool = dataclasses.field(pytree_node=False)

  shape1: Tuple[int, ...] = dataclasses.field(pytree_node=False)
  shape2: Tuple[int, ...] = dataclasses.field(pytree_node=False)

  batch_axis: int = dataclasses.field(pytree_node=False)
  channel_axis: int = dataclasses.field(pytree_node=False)

  mask1: Optional[np.ndarray]
  mask2: Optional[np.ndarray]

  def slice(self, n1_slice: slice, n2_slice: slice) -> 'Kernel':
    cov1 = self.cov1[n1_slice]
    cov2 = self.cov1[n2_slice] if self.cov2 is None else self.cov2[n2_slice]

    mask1 = None if self.mask1 is None else self.mask1[n1_slice]
    mask2 = None if self.mask2 is None else self.mask2[n2_slice]

    return self.replace(
        cov1=cov1,
        nngp=self.nngp[n1_slice, n2_slice],
        cov2=cov2,
        ntk=self.ntk[n1_slice, n2_slice],
        shape1=(cov1.shape[0],) + self.shape1[1:],
        shape2=(cov2.shape[0],) + self.shape2[1:],
        mask1=mask1,
        mask2=mask2)

  def reverse(self) -> 'Kernel':
    """Reverse the order of spatial axes in the covariance matrices.

    Returns:
      A `Kernel` object with spatial axes order flipped in
      all covariance matrices. For example, if `kernel.nngp` has shape
      `(batch_size_1, batch_size_2, H, H, W, W, D, D, ...)`, then
      `reverse(kernels).nngp` has shape
      `(batch_size_1, batch_size_2, ..., D, D, W, W, H, H)`.
    """
    # Number of spatial dimensions = total - (1 for batch + 1 for channels)
    ndim = len(self.shape1) - 2

    # ndim == 3: (-5, -6, -3, -4, -1, -2)
    source_axes = tuple(j for i in range(-ndim * 2, 0, 2) for j in (i + 1, i))

    # ndim == 3: (-1, -2, -3, -4, -5, -6)
    target_axes = tuple(range(-1, -ndim * 2 - 1, -1))

    def reverse(mat):
      if utils.is_array(mat):
        return np.moveaxis(mat, source_axes, target_axes)
      return mat

    cov1, nngp, cov2, ntk = map(reverse, (self.cov1,
                                          self.nngp,
                                          self.cov2,
                                          self.ntk))
    return self.replace(cov1=cov1, nngp=nngp, cov2=cov2, ntk=ntk,
                        is_reversed=not self.is_reversed)

  def transpose(self, axes: Tuple[int, ...] = None) -> 'Kernel':
    """Permute spatial dimensions of the `Kernel` according to `axes`.

    Follows
    https://docs.scipy.org/doc/numpy/reference/generated/numpy.transpose.html

    Note that `axes` apply only to spatial axes, batch axes are ignored and
    remain leading in all covariance arrays, and channel axes are not present
    in a `Kernel` object. If the covariance array is of shape
    `(batch_size, X, X, Y, Y)`, and `axes == (0, 1)`, resulting array is of
    shape `(batch_size, Y, Y, X, X)`.
    """
    if axes is None:
      axes = tuple(range(len(self.shape1) - 2))

    def permute(mat: Union[None, float, np.ndarray],
        batch_ndim: int) -> Union[None, float, np.ndarray]:
      if utils.is_array(mat):
        _axes = tuple(batch_ndim + a for a in axes)
        if not self.diagonal_spatial:
          _axes = tuple(j for a in _axes
                        for j in (2 * a - batch_ndim,
                                  2 * a - batch_ndim + 1))
        _axes = tuple(range(batch_ndim)) + _axes
        return np.transpose(mat, _axes)
      return mat

    cov1 = permute(self.cov1, 1 if self.diagonal_batch else 2)
    cov2 = permute(self.cov2, 1 if self.diagonal_batch else 2)
    nngp = permute(self.nngp, 2)
    ntk = permute(self.ntk, 2)
    return self.replace(cov1=cov1, nngp=nngp, cov2=cov2, ntk=ntk)

  def mask(self,
           mask1: Optional[np.ndarray],
           mask2: Optional[np.ndarray]) -> 'Kernel':
    """Mask all covariance matrices according to `mask1`, `mask2`"""
    mask11, mask12, mask22 = self._get_mask_prods(mask1, mask2)

    def mask_mat(mat, mask):
      if not utils.is_array(mat) or mask is None:
        return mat
      return np.where(mask, np.zeros((), mat.dtype), mat)

    cov1 = mask_mat(self.cov1, mask11)
    cov2 = mask_mat(self.cov2, mask22)
    nngp = mask_mat(self.nngp, mask12)
    ntk = mask_mat(self.ntk, mask12)

    return self.replace(cov1=cov1, nngp=nngp, cov2=cov2, ntk=ntk,
                        mask1=mask1, mask2=mask2)

  def _get_mask_prods(self,
                      mask1: Optional[np.ndarray],
                      mask2: Optional[np.ndarray]
  ) -> Tuple[Optional[np.ndarray],
             Optional[np.ndarray],
             Optional[np.ndarray]]:
    """Gets outer products of `mask1, mask1`, `mask1, mask2`, `mask2, mask2`."""
    def get_mask_prod(m1, m2, batch_ndim):
      if m1 is None and m2 is None:
        return None

      def reshape(m):
        if m is not None:
          if m.shape[self.channel_axis] != 1:
            raise NotImplementedError(
                f'Different channel-wise masks are not supported for '
                f'infinite-width layers now (got `mask.shape == {m.shape}). '
                f'Please describe your use case at '
                f'https://github.com/google/neural-tangents/issues/new')

          m = np.squeeze(np.moveaxis(m, (self.batch_axis, self.channel_axis),
                                     (0, -1)), -1)
          if self.is_reversed:
            m = np.moveaxis(m, range(1, m.ndim), range(m.ndim - 1, 0, -1))
        return m

      m1, m2 = reshape(m1), reshape(m2)

      start_axis = 2 - batch_ndim
      end_axis = 1 if self.diagonal_spatial else m1.ndim

      mask = utils.outer_prod(m1, m2, start_axis, end_axis, op.or_)
      return mask

    mask11 = get_mask_prod(mask1, mask1, 1 if self.diagonal_batch else 2)
    mask22 = (get_mask_prod(mask2, mask2, 1 if self.diagonal_batch else 2)
              if mask2 is not None else mask11)
    mask12 = get_mask_prod(mask1, mask2, 2)
    return mask11, mask12, mask22
