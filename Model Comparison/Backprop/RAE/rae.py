import torch
import torch.nn as nn
import torch.optim as optim
import torchvision
import matplotlib.pyplot as plt
import numpy as np
import random
import jax.numpy as jnp
import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from Backprop.GAN_Models.mnist_data import get_mnist_loaders
from ngclearn.utils.metric_utils import measure_MSE

# Set Random Seeds for Reproducibility
seed = 42
random.seed(seed)
np.random.seed(seed)
torch.manual_seed(seed)
if torch.cuda.is_available():
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False

# Device Configuration
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
print(f'Using device: {device}')

# Hyperparameters
latent_dim = 64
batch_size = 128
learning_rate = 0.0002
num_epochs = 100
l2_lambda = 1e-5  # Regularization parameter

train_loader, test_loader = get_mnist_loaders(batch_size=batch_size)

class Encoder(nn.Module):
    def __init__(self, latent_dim):
        super(Encoder, self).__init__()
        self.model = nn.Sequential(
            nn.Linear(28*28, 512),
            nn.ReLU(True),
            nn.Linear(512, latent_dim)
        )

    def forward(self, x):
        x = x.view(-1, 28*28)
        z = self.model(x)
        return z

class Decoder(nn.Module):
    def __init__(self, latent_dim):
        super(Decoder, self).__init__()
        self.model = nn.Sequential(
            nn.Linear(latent_dim, 512),
            nn.ReLU(True),
            nn.Linear(512, 28*28),
            nn.Tanh()
        )

    def forward(self, z):
        x_recon = self.model(z)
        x_recon = x_recon.view(-1, 1, 28, 28)
        return x_recon

class RegularizedAutoencoder:
    def __init__(self, latent_dim):
        self.encoder = Encoder(latent_dim).to(device)
        self.decoder = Decoder(latent_dim).to(device)
        self.reconstruction_loss = nn.MSELoss()
        self.optimizer = optim.Adam(
            list(self.encoder.parameters()) + list(self.decoder.parameters()), 
            lr=learning_rate, 
            weight_decay=l2_lambda
        )

    def binary_cross_entropy(self, p, x, preserve_batch=False, offset=1e-6):
        p_ = jnp.clip(p, offset, 1 - offset)
        bce = -jnp.sum(x * jnp.log(p_) + (1.0 - x) * jnp.log(1.0 - p_), axis=1, keepdims=True)
        if not preserve_batch:
            bce = jnp.mean(bce)
        return bce

    def negative_log_likelihood(self, p, x, preserve_batch=False, offset=1e-6):
        p_ = jnp.clip(p, offset, 1.0 - offset)
        loss = -(x * jnp.log(p_))
        nll = jnp.sum(loss, axis=1, keepdims=True)
        if not preserve_batch:
            nll = jnp.mean(nll)
        return nll

    def kullback_leibler_divergence(self, p_x, p_xHat, preserve_batch=False, offset=1e-6):
        _p_x = jnp.clip(p_x, offset, 1. - offset)
        _p_xHat = jnp.clip(p_xHat, offset, 1. - offset)
        N = p_x.shape[1]
        term1 = jnp.sum(_p_x * jnp.log(_p_x), axis=1, keepdims=True)
        term2 = -jnp.sum(_p_x * jnp.log(_p_xHat), axis=1, keepdims=True)
        kld = (term1 + term2) * (1 / N)
        if not preserve_batch:
            kld = jnp.mean(kld)
        return kld
    
    def eval_model(self, model, data_loader, mb_size, verbosity=1):
        model.encoder.eval()
        model.decoder.eval()
        n_batches = len(data_loader)
        n_samp_seen = 0
        nll = 0.0
        mse = 0.0
        bce = 0.0
        kld = 0.0

        with torch.no_grad():
            for i, (imgs, _) in enumerate(data_loader):
                real_imgs = imgs.to(device)
                z = model.encoder(real_imgs)
                recon_imgs = model.decoder(z)

                # Calculate reconstruction loss
                loss = model.reconstruction_loss(recon_imgs, real_imgs)

                # Convert tensors to numpy arrays for JAX calculations
                recon_imgs_flat = recon_imgs.view(-1, 28*28).cpu().numpy()
                real_imgs_flat = real_imgs.view(-1, 28*28).cpu().numpy()

                _nll = model.negative_log_likelihood(recon_imgs_flat, real_imgs_flat) * real_imgs.shape[0]
                _mse = measure_MSE(recon_imgs_flat, real_imgs_flat) * real_imgs.shape[0]
                _bce = model.binary_cross_entropy(recon_imgs_flat, real_imgs_flat) * real_imgs.shape[0]
                _kld = model.kullback_leibler_divergence(recon_imgs_flat, real_imgs_flat) * real_imgs.shape[0]

                nll += _nll
                mse += _mse
                bce += _bce
                kld += _kld

                n_samp_seen += real_imgs.shape[0]
                if verbosity > 0:
                    print("\r NLL = {} MSE = {} BCE = {} KLD = {} ({} samples)".format(
                        nll / n_samp_seen, mse / n_samp_seen, bce / n_samp_seen, kld / n_samp_seen, n_samp_seen), end="")
            if verbosity > 0:
                print()

        nll = nll / n_samp_seen
        mse = mse / n_samp_seen
        bce = bce / n_samp_seen
        kld = kld / n_samp_seen
        return nll, mse, bce, kld

    def train(self, train_loader, num_epochs):
        train_losses = []
        
        # Initialize lists to hold individual loss metrics
        nll_list, kld_list, bce_list,mse_list = [], [], [],[]

        for epoch in range(num_epochs):
            self.encoder.train()
            self.decoder.train()
            running_loss = 0.0
            
            for i, (imgs, _) in enumerate(train_loader):
                real_imgs = imgs.to(device)

                # Zero the gradients
                self.optimizer.zero_grad()

                # Forward pass
                z = self.encoder(real_imgs)
                recon_imgs = self.decoder(z)

                # Calculate reconstruction loss
                loss = self.reconstruction_loss(recon_imgs, real_imgs)

                # Convert tensors to numpy arrays for JAX calculations
                recon_imgs_flat = recon_imgs.view(-1, 28*28).detach().cpu().numpy()
                real_imgs_flat = real_imgs.view(-1, 28*28).detach().cpu().numpy()

                nll = self.negative_log_likelihood(recon_imgs_flat, real_imgs_flat)
                kld = self.kullback_leibler_divergence(recon_imgs_flat, real_imgs_flat)
                bce = self.binary_cross_entropy(recon_imgs_flat, real_imgs_flat)
                mse = measure_MSE(recon_imgs_flat, real_imgs_flat)

                # Backward pass and optimize
                loss.backward()
                self.optimizer.step()

                # Accumulate the loss
                running_loss += loss.item()
                
                # Store individual losses
                nll_list.append(nll.item())
                kld_list.append(kld.item())
                bce_list.append(bce.item())
                mse_list.append(mse.item())

                # Print progress for each batch
                if (i + 1) % 200 == 0:
                    print(f"Epoch [{epoch + 1}/{num_epochs}] Batch {i + 1}/{len(train_loader)} "
                          f"Loss: {loss.item():.4f} NLL: {nll.item():.4f} KLD: {kld.item():.4f} "
                          f"BCE: {bce.item():.4f} MSE: {mse.item():.4f}")


            train_losses.append(running_loss / len(train_loader))

            # Calculate and print average losses for the epoch
            avg_nll = np.mean(nll_list)
            avg_kld = np.mean(kld_list)
            avg_bce = np.mean(bce_list)
            avg_mse = np.mean(mse_list)

            print(f"Epoch [{epoch + 1}/{num_epochs}] "
                  f"Average Loss: {running_loss / len(train_loader):.4f}, "
                  f"Average NLL: {avg_nll:.4f}, "
                  f"Average KLD: {avg_kld:.4f}, "
                  f"Average BCE: {avg_bce:.4f}, "
                  f"Average MSE: {avg_mse:.4f}")

            # Reset lists for next epoch
            nll_list, kld_list, bce_list, mse_list = [], [], [],[]

            # Save reconstructed images every epoch
            self.save_reconstructed_images(recon_imgs, epoch)

        self.plot_training_loss(train_losses)

    def save_reconstructed_images(self, recon_imgs, epoch):
        self.encoder.eval()
        self.decoder.eval()
        
        with torch.no_grad():
            sample = recon_imgs[:16]
            sample = sample * 0.5 + 0.5  # Rescale images to [0, 1]
            grid = torchvision.utils.make_grid(sample, nrow=4)
            plt.imshow(grid.permute(1, 2, 0).cpu().numpy())
            plt.title(f'Epoch {epoch + 1}')
            plt.axis('off')
            plt.savefig(f'reconstructed_epoch_{epoch + 1}.png')
            plt.close()  # Close the figure to free memory

    def plot_training_loss(self, train_losses):
        plt.figure(figsize=(10, 5))
        plt.title("Training Loss During Regularized Autoencoder Training")
        plt.plot(train_losses, label='Training Loss')
        plt.xlabel('Epochs')
        plt.ylabel('Loss')
        plt.legend()
        plt.savefig('training_loss_curve.png')
        plt.close()

# Initialize and Train the Regularized Autoencoder
rae = RegularizedAutoencoder(latent_dim)
rae.train(train_loader, num_epochs)

# Evaluation on test data
nll, mse, bce, kld = rae.eval_model(rae, test_loader, mb_size=batch_size)
print(f"Test Results: NLL: {nll:.4f}, MSE: {mse:.4f}, BCE: {bce:.4f}, KLD: {kld:.4f}")