cmake_minimum_required(VERSION 3.12)
project(brbrdaemon VERSION 1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Determine version from Git
find_package(Git QUIET)

set(VERSION_MAJOR 1)
set(VERSION_MINOR 0)
set(VERSION_PATCH 0)
set(VERSION_BUILD 0)

if(GIT_FOUND)
    execute_process(
        COMMAND ${GIT_EXECUTABLE} rev-list --count HEAD
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        OUTPUT_VARIABLE GIT_COMMIT_COUNT
        OUTPUT_STRIP_TRAILING_WHITESPACE
        ERROR_QUIET
    )
    if(GIT_COMMIT_COUNT)
        set(VERSION_BUILD ${GIT_COMMIT_COUNT})
    endif()

    execute_process(
        COMMAND ${GIT_EXECUTABLE} describe --tags --match "v[0-9]*" --long --always
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        OUTPUT_VARIABLE GIT_DESCRIBE_OUT
        OUTPUT_STRIP_TRAILING_WHITESPACE
        ERROR_QUIET
    )

    if(GIT_DESCRIBE_OUT)
        if(GIT_DESCRIBE_OUT MATCHES "^v?([0-9]+)\\.([0-9]+)\\.([0-9]+)-([0-9]+)-g([0-9a-f]+)")
            set(VERSION_MAJOR "${CMAKE_MATCH_1}")
            set(VERSION_MINOR "${CMAKE_MATCH_2}")
            set(VERSION_PATCH "${CMAKE_MATCH_3}")
            set(VERSION_BUILD "${CMAKE_MATCH_4}")
        elseif(GIT_DESCRIBE_OUT MATCHES "^v?([0-9]+)\\.([0-9]+)-([0-9]+)-g([0-9a-f]+)")
            set(VERSION_MAJOR "${CMAKE_MATCH_1}")
            set(VERSION_MINOR "${CMAKE_MATCH_2}")
            set(VERSION_PATCH "0")
            set(VERSION_BUILD "${CMAKE_MATCH_3}")
        endif()
    endif()
endif()

message(STATUS "Building brbrdaemon version ${VERSION_MAJOR}.${VERSION_MINOR}.${VERSION_PATCH}.${VERSION_BUILD}")

set(FAVICON_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../favicon.ico")
file(TO_CMAKE_PATH "${FAVICON_PATH}" FAVICON_PATH)

# Generate version.rc from template
configure_file(
    ${CMAKE_CURRENT_SOURCE_DIR}/src/version.rc.in
    ${CMAKE_CURRENT_BINARY_DIR}/src/version.rc
    @ONLY
)

# Output directories
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)

# Static Core Library for Daemon, Runner, and Unit Tests
add_library(brbrdaemon_core STATIC
    src/ace_shared_memory.cpp
    src/ace_shared_memory.h
    src/telemetry_collector.cpp
    src/telemetry_collector.h
    src/sink.cpp
    src/sink.h
    src/config.cpp
    src/config.h
    src/safe_queue.h
    src/logger.h
    src/net_utils.cpp
    src/net_utils.h
)

target_link_libraries(brbrdaemon_core PUBLIC winhttp shell32 user32 winmm)

if(MSVC)
    target_compile_options(brbrdaemon_core PRIVATE /W4 /EHsc /permissive-)
endif()

# Main Daemon Target
add_executable(brbrdaemon
    src/main.cpp
    ${CMAKE_CURRENT_BINARY_DIR}/src/version.rc
)
target_link_libraries(brbrdaemon PRIVATE brbrdaemon_core)

# Mock Runner Target
add_executable(mock_runner
    src/mock_runner.cpp
    src/mock_shared_memory.cpp
    src/mock_shared_memory.h
)
target_link_libraries(mock_runner PRIVATE brbrdaemon_core)

# Unit Tests Target
add_executable(unit_tests
    src/unit_tests.cpp
    src/mock_shared_memory.cpp
    src/mock_shared_memory.h
)
target_link_libraries(unit_tests PRIVATE brbrdaemon_core)

if(MSVC)
    target_compile_options(brbrdaemon PRIVATE /W4 /EHsc /permissive-)
    target_link_options(brbrdaemon PRIVATE /INCREMENTAL:NO /SUBSYSTEM:WINDOWS /ENTRY:mainCRTStartup)

    target_compile_options(mock_runner PRIVATE /W4 /EHsc /permissive-)
    target_link_options(mock_runner PRIVATE /INCREMENTAL:NO)

    target_compile_options(unit_tests PRIVATE /W4 /EHsc /permissive-)
    target_link_options(unit_tests PRIVATE /INCREMENTAL:NO)
endif()

enable_testing()
add_test(NAME unit_tests COMMAND unit_tests)

add_custom_target(run_tests
    COMMAND ${CMAKE_CTEST_COMMAND} -C $<CONFIG> --output-on-failure
    DEPENDS unit_tests
    COMMENT "Running Assetto Corsa Evo test suite via CTest..."
)

add_custom_target(run_mock
    COMMAND mock_runner
    DEPENDS mock_runner
    COMMENT "Running Assetto Corsa Evo mock telemetry runner..."
)
