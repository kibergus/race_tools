cmake_minimum_required(VERSION 3.12)
project(BrBrDbTelemetry VERSION 1.0 LANGUAGES CXX)

# Require C++17 for thread safety features and standard compliance
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Configure output directories for built targets
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)

# Create a Shared Library target (DLL on Windows)
add_library(BrBrDbTelemetry SHARED 
    src/brbrdb_telemetry_plugin.cpp 
    src/brbrdb_telemetry_plugin.h
    src/sink.cpp
    src/sink.h
    src/config.cpp
    src/config.h
    src/safe_queue.h
    src/math_utils.cpp
    src/math_utils.h
    src/net_utils.cpp
    src/net_utils.h
    src/version.rc
)

# Apply specific suffix/naming rules matching rFactor 2 expectations
set_target_properties(BrBrDbTelemetry PROPERTIES
    PREFIX ""
    SUFFIX "64.dll"
)

# No external Windows-specific libraries needed: std::filesystem replaces shell32

# Add Windows specific link options (e.g. standard runtime libraries)
if(MSVC)
    target_compile_options(BrBrDbTelemetry PRIVATE /W4 /WX /EHsc)
    target_link_options(BrBrDbTelemetry PRIVATE /INCREMENTAL:NO)
endif()

# Create Mock Runner executable target
add_executable(mock_runner 
    src/mock_runner.cpp
    src/brbrdb_telemetry_plugin.cpp 
    src/brbrdb_telemetry_plugin.h
    src/sink.cpp
    src/sink.h
    src/config.cpp
    src/config.h
    src/safe_queue.h
    src/math_utils.cpp
    src/math_utils.h
    src/net_utils.cpp
    src/net_utils.h
)
if(MSVC)
    target_compile_options(mock_runner PRIVATE /W4 /WX /EHsc)
    target_link_options(mock_runner PRIVATE /INCREMENTAL:NO)
endif()

# Create Unit Tests executable target
add_executable(unit_tests 
    src/unit_tests.cpp
    src/config.cpp
    src/config.h
    src/safe_queue.h
    src/math_utils.cpp
    src/math_utils.h
    src/net_utils.cpp
    src/net_utils.h
    src/sink.cpp
    src/sink.h
)
if(MSVC)
    target_compile_options(unit_tests PRIVATE /W4 /WX /EHsc)
    target_link_options(unit_tests PRIVATE /INCREMENTAL:NO)
endif()

target_link_libraries(BrBrDbTelemetry PRIVATE winhttp)
target_link_libraries(mock_runner PRIVATE winhttp)
target_link_libraries(unit_tests PRIVATE winhttp)

enable_testing()
add_test(NAME unit_tests COMMAND unit_tests)

# Custom target to compile unit_tests and run ctest
add_custom_target(run_tests
    COMMAND ${CMAKE_CTEST_COMMAND} -C $<CONFIG> --output-on-failure
    DEPENDS unit_tests
    COMMENT "Running test suite via CTest..."
)

# Custom target to compile and run mock_runner
add_custom_target(run_mock
    COMMAND mock_runner
    DEPENDS mock_runner
    COMMENT "Running mock telemetry runner..."
)
