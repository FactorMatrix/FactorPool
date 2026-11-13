cmake_minimum_required(VERSION 3.28)
project(factorpool_research LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
find_package(Python3 REQUIRED COMPONENTS Interpreter Development.Module)
find_package(Threads REQUIRED)
Python3_add_library(_factorpool_native MODULE module.cpp analysis.cpp statistics.cpp portfolios.cpp decay.cpp research_input.cpp)
target_link_libraries(_factorpool_native PRIVATE Threads::Threads)
target_compile_options(_factorpool_native PRIVATE -Wall -Wextra -Wpedantic)

# Pool owns the optional Indicator extensions. The matrix backtest module above
# stays independent; existing Indicator primitives come from the pinned SDK.
if(FT_ROOT AND FT_BUILD)
  Python3_add_library(_factorpool_operators MODULE
    operator_binding.cpp operator_program.cpp operator_kernels.cpp
    calendar_window.cpp brain_temporal.cpp brain_cross.cpp)
  target_include_directories(_factorpool_operators PRIVATE
    "${FT_ROOT}/engine-core-cpp/mods/sys_indicator/include"
    "${FT_ROOT}/engine-core-cpp/mods/sys_indicator/src/batch_program")
  target_link_libraries(_factorpool_operators PRIVATE
    "${FT_BUILD}/native/abi/libfactortrader_c.so" Threads::Threads)
  target_compile_options(_factorpool_operators PRIVATE -Wall -Wextra -Wpedantic)
  add_executable(factorpool_brain_temporal_tests
    tests/brain_temporal_test.cpp brain_temporal.cpp)
  target_include_directories(factorpool_brain_temporal_tests PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
  target_compile_options(factorpool_brain_temporal_tests PRIVATE
    -Wall -Wextra -Wpedantic -Werror -UNDEBUG)
endif()
