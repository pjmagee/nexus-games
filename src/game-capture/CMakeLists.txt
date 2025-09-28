cmake_minimum_required(VERSION 3.20)

# Prevent in-source builds
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_BINARY_DIR)
    message(FATAL_ERROR "In-source build disallowed. Use: cmake -S . -B build")
endif()

project(hots_capture
    VERSION 1.0.0
    DESCRIPTION "Heroes of the Storm Game Capture Service"
    LANGUAGES CXX
)

# Modern C++ standard
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Create the main executable
add_executable(hots_capture src/main.cpp)

# Set target properties using modern CMake
target_compile_features(hots_capture PRIVATE cxx_std_20)

# Platform-specific compile definitions
target_compile_definitions(hots_capture PRIVATE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
    _CRT_SECURE_NO_WARNINGS
    UNICODE
    _UNICODE
)

# System libraries
target_link_libraries(hots_capture PRIVATE
    d3d11
    dxgi
    WindowsApp
)

# Compiler-specific options
if(MSVC)
    target_compile_options(hots_capture PRIVATE
        /W4           # High warning level
        /permissive-  # Disable non-conforming code
        /EHsc         # Exception handling model
        /utf-8        # Source and execution character sets are UTF-8
    )
    # Enable additional security features in Release builds
    target_compile_options(hots_capture PRIVATE
        $<$<CONFIG:Release>:/guard:cf>  # Control Flow Guard
        $<$<CONFIG:Release>:/Qspectre>  # Spectre mitigation (if available)
    )
endif()

# Output directory configuration
set_target_properties(hots_capture PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
    RUNTIME_OUTPUT_DIRECTORY_DEBUG "${CMAKE_BINARY_DIR}/bin/Debug"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_BINARY_DIR}/bin/Release"
    RUNTIME_OUTPUT_DIRECTORY_RELWITHDEBINFO "${CMAKE_BINARY_DIR}/bin/RelWithDebInfo"
    RUNTIME_OUTPUT_DIRECTORY_MINSIZEREL "${CMAKE_BINARY_DIR}/bin/MinSizeRel"
)

# Code formatting setup
find_program(CLANG_FORMAT_EXE NAMES clang-format clang-format.exe)
if(CLANG_FORMAT_EXE)
    message(STATUS "clang-format found: ${CLANG_FORMAT_EXE}")

    # Collect all source files for formatting
    file(GLOB_RECURSE ALL_SOURCE_FILES
        "${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp"
        "${CMAKE_CURRENT_SOURCE_DIR}/src/*.h"
        "${CMAKE_CURRENT_SOURCE_DIR}/src/*.hpp"
    )

    add_custom_target(format
        COMMAND ${CLANG_FORMAT_EXE} -i ${ALL_SOURCE_FILES}
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        COMMENT "Formatting source code with clang-format"
        VERBATIM
    )

    add_custom_target(format-check
        COMMAND ${CLANG_FORMAT_EXE} --dry-run --Werror ${ALL_SOURCE_FILES}
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        COMMENT "Checking code format with clang-format (no changes)"
        VERBATIM
    )
else()
    message(STATUS "clang-format not found - format targets unavailable")
endif()

# Additional development targets
add_custom_target(clean-build
    COMMAND ${CMAKE_COMMAND} -E remove_directory "${CMAKE_BINARY_DIR}"
    COMMENT "Cleaning build directory"
)
