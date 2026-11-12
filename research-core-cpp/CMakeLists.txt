cmake_minimum_required(VERSION 3.28)
project(factorpool_research LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
find_package(Python3 REQUIRED COMPONENTS Interpreter Development.Module)
Python3_add_library(_factorpool_native MODULE src/module.cpp src/analysis.cpp src/statistics.cpp src/portfolios.cpp src/decay.cpp src/research_input.cpp)
target_compile_options(_factorpool_native PRIVATE -Wall -Wextra -Wpedantic)

# Pool owns the optional Indicator extensions. The matrix backtest module above
# stays independent; existing Indicator primitives come from the pinned SDK.
if(FT_ROOT AND FT_BUILD)
  Python3_add_library(_factorpool_operators MODULE
    src/operator_binding.cpp src/operator_program.cpp src/operator_kernels.cpp
    src/calendar_window.cpp)
  target_include_directories(_factorpool_operators PRIVATE
    "${FT_ROOT}/engine-core-cpp/mods/sys_indicator/include"
    "${FT_ROOT}/engine-core-cpp/mods/sys_indicator/src/batch_program")
  target_link_libraries(_factorpool_operators PRIVATE
    "${FT_BUILD}/native/abi/libfactortrader_c.so")
  target_compile_options(_factorpool_operators PRIVATE -Wall -Wextra -Wpedantic)
endif()
