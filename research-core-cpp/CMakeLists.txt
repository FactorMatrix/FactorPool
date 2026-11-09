cmake_minimum_required(VERSION 3.28)
project(factorpool_research LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
find_package(Python3 REQUIRED COMPONENTS Interpreter Development.Module)
set(FT_ROOT "" CACHE PATH "Pinned FactorTrader checkout")
set(FT_BUILD "" CACHE PATH "Matching FactorTrader SDK build")
Python3_add_library(_factorpool_native MODULE src/module.cpp src/analysis.cpp src/provider.cpp)
target_include_directories(_factorpool_native PRIVATE
  ${FT_ROOT}/engine-core-cpp/include ${FT_ROOT}/engine-core-cpp/mods/include
  ${FT_ROOT}/engine-sdk/mods/cn-data-mmap-mod/include)
target_link_libraries(_factorpool_native PRIVATE ${FT_BUILD}/native/abi/libfactortrader_c.so)
set_target_properties(_factorpool_native PROPERTIES BUILD_RPATH "${FT_BUILD}/native/abi" INSTALL_RPATH "$ORIGIN/lib")
target_compile_options(_factorpool_native PRIVATE -Wall -Wextra -Wpedantic)
