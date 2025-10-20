#!/bin/bash


# Here are all the global variqables used in this script
aws_profile=""
aws_region=""

# Parse the file and extract the sections within square brackets
extract_sections() {
    local file="$1"
    grep -oP '\[\K[^]]+' "$file"
}

# Display the AWS Profile Selection menu
display_aws_profile_menu() {
    local file="$1"
    local options=()
    local i=1
    echo "Select the AWS Profile to use:"
    while IFS= read -r section; do
        echo "$i. $section"
        options+=("$section")
        ((i++))
    done < <(extract_sections "$file")
    echo "0. Exit"
}


# Find the line number of a text enclosed in square brackets
find_line_number() {
    local file="$1"
    local search_text="$2"

    # Use grep to search for the text enclosed in square brackets and get its line number
    local line_number
    line_number=$(grep -n "\[$search_text\]" "$file" | cut -d: -f1)

    # Check if the search text was found
    if [[ -n "$line_number" ]]; then
        echo $line_number
    else
        echo "0"
    fi
}

# Utility to return the line following a specific line in a file
get_next_line() {
    local file="$1"
    local line_number="$2"

    # Use awk to print the line following the specified line number
    awk "NR == $((line_number + 1))" "$file"
}

# Using the profile, retrieve the associated aws region.
get_aws_region() {
    local file="$1"
    local search_text="$2"

    # Find the line number of the search text
    local line_number
    line_number=$(find_line_number "$file" "$search_text")

    # echo "I: Line number is $line_number"


    # Check if the search text was found
    if [[ "$line_number" != "0" ]]; then
        # Get the next line
        local next_line
        next_line=$(get_next_line "$file" "$line_number")

        # Print the next line
        # echo "I: Next line after '$search_text': '$next_line'"
        local region
        aws_region=$(echo "$next_line" | awk '{print $NF}')
        # echo "I: Region is: $aws_region"
    else
        echo "Search text '$search_text' not found in the $file."
        exit 1
    fi
}

# Get the AWS profile
get_aws_profile() {
    local file="$1"

    local chosen_option
    read -rp "Enter your choice: " chosen_option

    if [[ $chosen_option -eq 0 ]]; then
        echo "Exiting..."
        exit 0
    fi

    local selected_section
    selected_section=$(extract_sections "$file" | sed -n "${chosen_option}p")
    aws_profile=$selected_section
    # echo "I: Selected: $aws_profile"

    if [[ -z $aws_profile ]]; then
        echo "Invalid choice. Please select a valid profile."
        exit 0
    fi

    echo 
}

# Retrieve the instance types and filter based on preference
retrieve_list() {
    local node_type=$1
    rosa list instance-types --profile="$aws_profile" --region="$aws_region" | grep "$node_type"
}

# Display the interactive menu to get the instnce type
display_machine_instance_types_menu() {
    local options=()
    local i=1
    echo "Retrieving available '$node_type' machine-instance types..."
    echo
    while IFS= read -r line; do
        echo "$i. $line"
        options+=("$line")
        ((i++))
    done < <(retrieve_list "$1")
    echo "0. Exit"
}

# Prompt for node type
prompt_node_type() {
    local node_type
    while true; do
        read -rp "Do you want bare [M]etal or [G]PU-enabled nodes? (M/G): " node_type_choice

        case "$node_type_choice" in
            [Mm]) node_type="metal" && break ;;
            [Gg]) node_type="accelerated" && break ;;
    	*) echo "Invalid choice. Please enter M or G." ;;
        esac
    done

    echo "$node_type"
}

# Prompt for number of replicas
prompt_replicas() {
    local replicas
    while true; do
        read -rp "Enter the number of replicas (0-5): " replicas

        if [[ $replicas =~ ^[0-5]$ ]]; then
            break
        else
            echo "Invalid input. Please enter a number between 0 and 5."
        fi
    done

    echo "$replicas" # Return the number of replicas
}

provision_machine_pool() {
    local ec2_instance_type
    local replicas
    ec2_instance_type=$1
    replicas=$2

    # Retrieve the output of 'rosa list clusters' - skip the heading
    local cluster_list
    # cluster_list=$(rosa list clusters --profile="$aws_profile" --region="$aws_region" | tail -n +2)
    cluster_list=$(rosa list clusters --profile="$aws_profile"  | tail -n +2)
    echo $cluster_list

    # Count the number of lines in the output
    local num_clusters
    num_clusters=$(echo "$cluster_list" | wc -l)
    echo "Number of clusters is $num_clusters"

    # Check if there is exactly one cluster listed
    if [ "$num_clusters" -ne 1 ]; then
        echo "Error: There should be exactly one cluster listed."
        exit 1
    fi

    # Capture the cluster ID from the output
    local cluster_id
    cluster_id=$(echo "$cluster_list" | awk ' {print $1}')
    echo "Cluster ID: $cluster_id"

    # Check if cluster ID is empty or null
    if [ -z "$cluster_id" ]; then
        echo "Failed to retrieve cluster ID. Aborting."
        exit 1
    fi

    # Create machine pool using the captured cluster ID
    local machinepool
    machinepool="${ec2_instance_type//./-}-mp"     # Create a machine pool name using the instance type. Replace . with -
    echo "Adding the instance type $ec2_instance_type to the cluster $cluster_id. Machine pool name $machinepool"
    rosa create machinepool --cluster="$cluster_id" --profile="$aws_profile" --region="$aws_region" --name="$machinepool" --replicas=0 --instance-type="$ec2_instance_type" --disk-size=128GiB

    echo
    local choice
    read -p "Do you want to add the $replicas replica(s) of the instance type: $ec2_instance_type now? (Y/N): " choice
    if [ "$choice" = "Y" ] || [ "$choice" = "y" ]; then
        # Scale up the machine pool - (demonstrating the other important ROSA command)
        echo "Setting replica count to $replicas"
        rosa edit machinepool --cluster="$cluster_id" --profile="$aws_profile" --region="$aws_region" --replicas="$replicas" --labels="custom-machine-type"="$machinepool" "$machinepool"
    fi

    echo
    rosa list machinepools --cluster="$cluster_id" --profile="$aws_profile" --region="$aws_region" 
}

get_instance_type() {
    local chosen_instance
    read -rp "Select instance type: " chosen_instance

    if [[ $chosen_instance -eq 0 ]]; then
        echo "Exiting..."
        exit 0
    fi

    local selection
    selection=$(retrieve_list "$node_type" | sed -n "${chosen_instance}p")

    echo $selection
}

# Main function
main() {
    echo "Script to add a bare metal or GPU-enabed worker node to a ROSA cluster."
    echo
    echo "This script requires that you only have one ROSA cluster in your account."
    echo "Please ensure that you have the AWS v2 and ROSA CLI installed and configured."
    echo 
    echo "If you have multiple accounts configured in the AWS CLI then the scrtipt "
    echo "will prompt you to select which account you want to work with."
    echo 

    local aws_credentials_file="$HOME/.aws/credentials"
    local aws_config_file="$HOME/.aws/config"

    display_aws_profile_menu "$aws_credentials_file"

    local profile
    get_aws_profile "$aws_credentials_file"
    echo "Selected profile: $aws_profile"

    get_aws_region "$aws_config_file" "$aws_profile"
    echo "Region for profile $aws_profile is: $aws_region"
    echo

    local node_type
    node_type=$(prompt_node_type)
    echo "Selected node type: $node_type"

    display_machine_instance_types_menu "$node_type"

    local selection
    selection=$(get_instance_type)



    # Extracting the first column from the selection
    local aws_instance_type
    aws_instance_type=$(echo "$selection" | awk '{print $1}')

    echo "You selected: $aws_instance_type"

    local replicas
    replicas=$(prompt_replicas)

    echo "Number of replicas: $replicas"

    provision_machine_pool "$aws_instance_type" "$replicas"
}

# Execute the main function
main


