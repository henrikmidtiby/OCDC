import os
import pathlib
from abc import ABC, abstractmethod
from typing import Any

import numpy as np
import rasterio  # type: ignore[import-untyped]
from numpy.typing import NDArray
from sklearn import mixture  # type: ignore[import-untyped]


class ReferencePixels:
    def __init__(
        self, *, reference: pathlib.Path, annotated: pathlib.Path, bands_to_use: tuple[int, ...] | None, **kwargs: Any
    ):
        self.reference_image_filename = reference
        self.mask_filename = annotated
        self.bands_to_use: tuple[int, ...] | None = bands_to_use
        self.reference_image: NDArray[Any] = np.zeros(0)
        self.mask: NDArray[Any] = np.zeros(0)
        self.values: NDArray[Any] = np.zeros(0)
        self.initialize()

    def initialize(self) -> None:
        self.load_reference_image(self.reference_image_filename)
        self.load_mask(self.mask_filename)
        if self.bands_to_use is None:
            self.bands_to_use = tuple(range(self.reference_image.shape[0] - 1))
        self.generate_pixel_mask()
        self.show_statistics_of_pixel_mask()

    def load_reference_image(self, filename_reference_image: pathlib.Path) -> None:
        with rasterio.open(filename_reference_image) as ref_img:
            self.reference_image = ref_img.read()

    def load_mask(self, filename_mask: pathlib.Path) -> None:
        with rasterio.open(filename_mask) as msk:
            self.mask = msk.read()

    def generate_pixel_mask(
        self, lower_range: tuple[int, int, int] = (245, 0, 0), higher_range: tuple[int, int, int] = (256, 10, 10)
    ) -> None:
        if self.mask.shape[0] == 3 or self.mask.shape[0] == 4:
            pixel_mask = np.where(
                (self.mask[0, :, :] > lower_range[0])
                & (self.mask[0, :, :] < higher_range[0])
                & (self.mask[1, :, :] > lower_range[1])
                & (self.mask[1, :, :] < higher_range[1])
                & (self.mask[2, :, :] > lower_range[2])
                & (self.mask[2, :, :] < higher_range[2]),
                255,
                0,
            )
        elif self.mask.shape[0] == 1:
            pixel_mask = np.where((self.mask[0, :, :] > 127), 255, 0)
        else:
            raise Exception(f"Expected a Black and White or RGB image for mask but got {self.mask.shape[0]} Bands")
        self.values = self.reference_image[:, pixel_mask == 255]
        self.values = self.values[self.bands_to_use, :]

    def show_statistics_of_pixel_mask(self) -> None:
        print(f"Number of annotated pixels: { self.values.shape }")
        min_annotated_pixels = 100
        if self.values.shape[1] <= min_annotated_pixels:
            raise Exception(
                f"Not enough annotated pixels. Need at least {min_annotated_pixels}, but got {self.values.shape[1]}"
            )

    def save_pixel_values_to_file(self, filename: pathlib.Path) -> None:
        # fix header for csv file
        output_directory = os.path.dirname(filename)
        if not os.path.isdir(output_directory):
            os.makedirs(output_directory)
        print(f'Writing pixel values to the file "{ filename }"')
        np.savetxt(
            filename,
            self.values.transpose(),
            delimiter="\t",
            # fmt="%i",
            # header=self.color_space.color_space[0]
            # + "\t"
            # + self.color_space.color_space[1]
            # + "\t"
            # + self.color_space.color_space[2],
            comments="",
        )


class BaseDistance(ABC):
    """Base class for all color distance models."""

    def __init__(self, **kwargs: Any):
        self.reference_pixels = ReferencePixels(**kwargs)
        self.bands_to_use = self.reference_pixels.bands_to_use

    def initialize(self) -> None:
        self.calculate_statistics()
        self.show_statistics()

    def save_pixel_values(self, filename: pathlib.Path) -> None:
        self.reference_pixels.save_pixel_values_to_file(filename)

    @abstractmethod
    def calculate_statistics(self) -> None:
        pass

    @abstractmethod
    def calculate_distance(self, image: NDArray[Any]) -> NDArray[Any]:
        pass

    @abstractmethod
    def show_statistics(self) -> None:
        pass


class MahalanobisDistance(BaseDistance):
    """
    A multivariate normal distribution used to describe the color of a set of
    pixels.
    """

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

    def calculate_statistics(self) -> None:
        self.covariance: NDArray[Any] = np.cov(self.reference_pixels.values)
        self.average = np.average(self.reference_pixels.values, axis=1)

    def calculate_distance(self, image: NDArray[Any]) -> NDArray[Any]:
        """
        For all pixels in the image, calculate the Mahalanobis distance
        to the reference color.
        """
        assert self.bands_to_use is not None
        pixels = np.reshape(image[self.bands_to_use, :, :], (len(self.bands_to_use), -1)).transpose()
        inv_cov = np.linalg.inv(self.covariance)
        diff = pixels - self.average
        modified_dot_product = diff * (diff @ inv_cov)
        distance = np.sum(modified_dot_product, axis=1)
        distance = np.sqrt(distance)
        distance_image = np.reshape(distance, (1, image.shape[1], image.shape[2]))
        return distance_image

    def show_statistics(self) -> None:
        print("Average color value of annotated pixels")
        print(self.average)
        print("Covariance matrix of the annotated pixels")
        print(self.covariance)


class GaussianMixtureModelDistance(BaseDistance):
    def __init__(self, n_components: int, **kwargs: Any):
        super().__init__(**kwargs)
        self.n_components = n_components

    def calculate_statistics(self) -> None:
        self.gmm = mixture.GaussianMixture(n_components=self.n_components, covariance_type="full")
        self.gmm.fit(self.reference_pixels.values.transpose())
        self.average = self.gmm.means_
        self.covariance = self.gmm.covariances_

    def calculate_distance(self, image: NDArray[Any]) -> NDArray[Any]:
        """
        For all pixels in the image, calculate the distance to the
        reference color modelled as a Gaussian Mixture Model.
        """
        assert self.bands_to_use is not None
        pixels = np.reshape(image[self.bands_to_use, :, :], (len(self.bands_to_use), -1)).transpose()
        loglikelihood = self.gmm.score_samples(pixels)
        # distance = np.exp(loglikelihood) # to make the values positive.
        distance_image = np.reshape(loglikelihood, (1, image.shape[1], image.shape[2]))
        return distance_image

    def show_statistics(self) -> None:
        print("GMM")
        print(self.gmm)
        print(self.gmm.means_)
        print(self.gmm.covariances_)
