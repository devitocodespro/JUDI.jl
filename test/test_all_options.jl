# Author: Mathias Louboutin, mlouboutin3@gatech.edu
# Date: July 2020

### Model
model, model0, dm = setup_model(tti, viscoacoustic, nlayer)
q, srcGeometry, recGeometry, f0 = setup_geom(model)
dt = srcGeometry.dt[1]

@testset "Gradient options test with $(nlayer) layers and tti $(tti) and freesurface $(fs)" begin
        ##################################ISIC########################################################
        printstyled("Testing isic \n"; color = :blue)
        @timeit TIMEROUTPUT "ISIC" begin
                opt = Options(sum_padding=true, free_surface=fs, IC="isic", f0=f0)
                F = judiModeling(model0, srcGeometry, recGeometry; options=opt)

                # Linearized modeling
                J = judiJacobian(F, q)
                @test norm(J*(0f0.*dm)) == 0

                y0 = F*q
                y_hat = J*dm
                x_hat1 = adjoint(J)*y0

                c = dot(y0, y_hat)
                d = dot(dm, x_hat1)
                @printf(" <J x, y> : %2.5e, <x, J' y> : %2.5e, relative error : %2.5e \n", c, d, c/d - 1)
                @test isapprox(c, d, rtol=5f-2)
                @test !isnan(norm(y0))
                @test !isnan(norm(y_hat))
                @test !isnan(norm(x_hat1))
        end
        ##################################ISIC########################################################
        printstyled("Testing fwi \n"; color = :blue)
        @timeit TIMEROUTPUT "fwi" begin
                opt = Options(sum_padding=true, free_surface=fs, IC="fwi", f0=f0)
                F = judiModeling(model0, srcGeometry, recGeometry; options=opt)

                # Linearized modeling
                J = judiJacobian(F, q)
                @test norm(J*(0f0.*dm)) == 0

                y0 = F*q
                y_hat = J*dm
                x_hat1 = adjoint(J)*y0

                c = dot(y0, y_hat)
                d = dot(dm, x_hat1)
                @printf(" <J x, y> : %2.5e, <x, J' y> : %2.5e, relative error : %2.5e \n", c, d, c/d - 1)
                @test isapprox(c, d, rtol=5f-2)
                @test !isnan(norm(y0))
                @test !isnan(norm(y_hat))
                @test !isnan(norm(x_hat1))
        end
        ##################################checkpointing###############################################
        printstyled("Testing checkpointing \n"; color = :blue)
        @timeit TIMEROUTPUT "Checkpointing" begin
                opt = Options(sum_padding=true, free_surface=fs, optimal_checkpointing=true, f0=f0)
                F = judiModeling(model0, srcGeometry, recGeometry; options=opt)

                # Linearized modeling
                J = judiJacobian(F, q)

                y_hat = J*dm
                x_hat2 = adjoint(J)*y0

                c = dot(y0, y_hat)
                d = dot(dm, x_hat2)
                @printf(" <J x, y> : %2.5e, <x, J' y> : %2.5e, relative error : %2.5e \n", c, d, c/d - 1)
                @test isapprox(c, d, rtol=1f-2)

                @test !isnan(norm(y_hat))
                @test !isnan(norm(x_hat2))
        end

        ##################################DFT#########################################################
        printstyled("Testing DFT \n"; color = :blue)
        @timeit TIMEROUTPUT "DFT" begin
                opt = Options(sum_padding=true, free_surface=fs, frequencies=[2.5, 4.5], f0=f0)
                F = judiModeling(model0, srcGeometry, recGeometry; options=opt)

                # Linearized modeling
                J = judiJacobian(F, q)
                @test norm(J*(0f0.*dm)) == 0

                y_hat = J*dm
                x_hat3 = adjoint(J)*y0

                c = dot(y0, y_hat)
                d = dot(dm, x_hat3)
                @printf(" <J x, y> : %2.5e, <x, J' y> : %2.5e, relative error : %2.5e \n", c, d, c/d - 1)
                @test !isnan(norm(y_hat))
                @test !isnan(norm(x_hat3))
        end

        ################################## DFT time subsampled#########################################
        printstyled("Testing subsampled in time DFT \n"; color = :blue)
        @timeit TIMEROUTPUT "Subsampled DFT" begin
                opt = Options(sum_padding=true, free_surface=fs, frequencies=[2.5, 4.5], dft_subsampling_factor=4, f0=f0)
                F = judiModeling(model0, srcGeometry, recGeometry; options=opt)

                # Linearized modeling
                J = judiJacobian(F, q)
                @test norm(J*(0f0.*dm)) == 0

                y_hat = J*dm
                x_hat3 = adjoint(J)*y0

                c = dot(y0, y_hat)
                d = dot(dm, x_hat3)
                @printf(" <J x, y> : %2.5e, <x, J' y> : %2.5e, relative error : %2.5e \n", c, d, c/d - 1)
                @test !isnan(norm(y_hat))
                @test !isnan(norm(x_hat3))
        end

        ##################################subsampling#################################################
        printstyled("Testing subsampling \n"; color = :blue)
        @timeit TIMEROUTPUT "Subsampling" begin
                opt = Options(sum_padding=true, free_surface=fs, subsampling_factor=4, f0=f0)
                F = judiModeling(model0, srcGeometry, recGeometry; options=opt)

                # Linearized modeling
                J = judiJacobian(F, q)

                y_hat = J*dm
                x_hat4 = adjoint(J)*y0

                c = dot(y0, y_hat)
                d = dot(dm, x_hat4)
                @printf(" <J x, y> : %2.5e, <x, J' y> : %2.5e, relative error : %2.5e \n", c, d, c/d - 1)
                @test isapprox(c, d, rtol=1f-2)
                @test !isnan(norm(y_hat))
                @test !isnan(norm(x_hat4))
        end
        ##################################ISIC + DFT #########################################################
        printstyled("Testing isic+dft \n"; color = :blue)
        @timeit TIMEROUTPUT "ISIC+DFT" begin
                opt = Options(sum_padding=true, free_surface=fs, IC="isic", frequencies=[2.5, 4.5], f0=f0)
                F = judiModeling(model0, srcGeometry, recGeometry; options=opt)

                # Linearized modeling
                J = judiJacobian(F, q)
                @test norm(J*(0f0.*dm)) == 0

                y_hat = J*dm
                x_hat5 = adjoint(J)*y0

                c = dot(y0, y_hat)
                d = dot(dm, x_hat5)
                @printf(" <J x, y> : %2.5e, <x, J' y> : %2.5e, relative error : %2.5e \n", c, d, c/d - 1)
                @test !isnan(norm(y_hat))
                @test !isnan(norm(x_hat5))
        end

        ##################################fwi + DFT #########################################################
        printstyled("Testing fwi+dft \n"; color = :blue)
        @timeit TIMEROUTPUT "FWI+DFT" begin
                opt = Options(sum_padding=true, free_surface=fs, IC="fwi", frequencies=[2.5, 4.5], f0=f0)
                F = judiModeling(model0, srcGeometry, recGeometry; options=opt)

                # Linearized modeling
                J = judiJacobian(F, q)
                @test norm(J*(0f0.*dm)) == 0

                y_hat = J*dm
                x_hat5 = adjoint(J)*y0

                c = dot(y0, y_hat)
                d = dot(dm, x_hat5)
                @printf(" <J x, y> : %2.5e, <x, J' y> : %2.5e, relative error : %2.5e \n", c, d, c/d - 1)
                @test !isnan(norm(y_hat))
                @test !isnan(norm(x_hat5))
        end
end
